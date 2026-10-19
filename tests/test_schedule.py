import importlib.util
import json
import plistlib
import subprocess
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from index_valuation import cli
from index_valuation.registry import load_indices

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load_script("install_schedule")
runner = load_script("run_scheduled")


@pytest.mark.parametrize("markets", [("CN", "HK"), ("US",), None])
def test_market_update_includes_new_indices_without_touching_other_markets(
    markets, tmp_path, monkeypatch
):
    specs = load_indices()
    specs["future_us"] = replace(specs["nasdaq100"], key="future_us")
    settings = SimpleNamespace(
        indices_file=ROOT / "indices.toml", data_dir=tmp_path, source_timeout=1
    )
    collected, returns_collected, built, paused = [], [], [], []
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "load_indices", lambda _: specs)
    monkeypatch.setattr(cli, "build_engine", lambda _: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(
        cli,
        "Repository",
        lambda _: SimpleNamespace(
            index_series=lambda *_: [],
            ensure_index=lambda *_: None,
            lock=lambda *_: nullcontext(),
            start_run=lambda *_: uuid4(),
            finish_run=lambda run, status, report: paused.append(report),
        ),
    )
    monkeypatch.setattr(cli, "Pipeline", lambda *_: None)

    def no_yahoo(*_args, **_kwargs):
        raise AssertionError("Disabled Yahoo sources must not be constructed")

    monkeypatch.setattr(cli, "YahooHistory", no_yahoo)
    monkeypatch.setattr(cli, "YahooETFValuation", no_yahoo)

    def collect(spec, *_args, **_kwargs):
        collected.append(spec.key)

    def refresh(spec):
        built.append(spec.key)
        return {"status": "collected", "index": spec.key}

    def collect_returns(spec, **kwargs):
        assert kwargs["incremental"] is True
        assert kwargs["start"] is None
        returns_collected.append(spec.key)
        return {"status": "collected", "index": spec.key}

    def no_external_requests(*_args, **_kwargs):
        raise AssertionError("Scheduling unit tests must not request external return data")

    monkeypatch.setattr(
        cli,
        "TotalReturnSources",
        lambda *_: SimpleNamespace(history=no_external_requests, trading_days=no_external_requests),
    )
    monkeypatch.setattr(
        cli, "TotalReturnCollector", lambda *_: SimpleNamespace(run=collect_returns)
    )
    monkeypatch.setattr(
        cli, "HistoricalBackfill", lambda *_args, **_kwargs: SimpleNamespace(run=collect)
    )
    monkeypatch.setattr(cli, "IndexSeries", lambda *_: SimpleNamespace(refresh=refresh))
    selected = [s for s in specs.values() if markets is None or s.market in markets]
    command = ["update-index", "--market", *markets] if markets else ["update-index", "--all"]
    assert cli.main(command) == (2 if any(not s.collection_enabled for s in selected) else 0)
    expected = [s.key for s in selected if s.collection_enabled]
    assert collected == built == expected
    assert returns_collected == [s.key for s in selected if s.collection_enabled and s.total_return]
    assert ("future_us" in collected) == (markets is None or "US" in markets)
    assert ("future_us" in returns_collected) == (markets is None or "US" in markets)
    assert [r["index"] for r in paused] == [s.key for s in selected if not s.collection_enabled]
    for report in paused:
        assert report["status"] == "blocked" and report["sources"] == {}
        assert report["archived_method"] == cli.method_for(specs[report["index"]])
        assert Path(report["report_path"]).exists()


@pytest.mark.parametrize("extra", [["--all"], ["--index", "csi300"], ["invalid"]])
def test_market_selector_rejects_ambiguous_or_invalid_scope(extra):
    with pytest.raises(SystemExit) as error:
        cli.main(["update-index", "--market", "US", *extra])
    assert error.value.code == 2


def test_daily_jobs_forward_separate_market_scopes_and_preserve_partial_status(
    tmp_path, monkeypatch
):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    jobs = installer.job_definitions(tmp_path)
    expected = [(17, ["CN", "HK"]), (9, ["US"])]
    for job, (hour, markets) in zip(jobs, expected, strict=True):
        # No weekday/month/day restriction: includes weekends and holiday retries.
        assert job["StartCalendarInterval"] == {"Hour": hour, "Minute": 0}
        assert runner.main(job["ProgramArguments"][2:]) == 2
        command, kwargs = calls[-1]
        assert command[1:] == ["update-index", "--market", *markets]
        assert kwargs["timeout"] == 3600 and kwargs["cwd"] == tmp_path
        receipt = (
            tmp_path
            / "data/scheduler/index-runs"
            / ("asia.latest.json" if hour == 17 else "us.latest.json")
        )
        record = json.loads(receipt.read_text())
        assert record["status"] == "partial" and record["exit_code"] == 2
        assert record["started_at"] <= record["finished_at"]
    assert len({job["StandardOutPath"] for job in jobs}) == 2


def test_scheduled_timeout_is_reported(tmp_path, monkeypatch):
    def run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    assert runner.main(["--market", "US"]) == 124
    record = json.loads((tmp_path / "data/scheduler/index-runs/us.latest.json").read_text())
    assert record["status"] == "timed_out" and record["exit_code"] == 124


def test_scheduled_receipt_retains_run_ids_and_stdout_even_on_partial_failure(
    tmp_path, monkeypatch, capsys
):
    report = {"mode": "direct_index", "run_id": str(uuid4()), "status": "collected"}
    paused = {"mode": "direct_index_collection_paused", "run_id": str(uuid4())}

    def run(command, **kwargs):
        record = json.loads((tmp_path / "data/scheduler/index-runs/us.latest.json").read_text())
        assert record["status"] == "running" and record["finished_at"] is None
        kwargs["stdout"].write("provider noise\n{not JSON}\n")
        kwargs["stdout"].write(json.dumps(report) + "\n" + json.dumps(paused))
        return SimpleNamespace(returncode=2)

    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main(["--market", "US"]) == 2
    record = json.loads((tmp_path / "data/scheduler/index-runs/us.latest.json").read_text())
    assert record["run_ids"] == [report["run_id"], paused["run_id"]]
    assert report["run_id"] in capsys.readouterr().out


def generated_jobs(root):
    files = []
    for job in installer.job_definitions(root):
        path = root / f"{job['Label']}.plist"
        path.write_bytes(plistlib.dumps(job))
        files.append(path)
    return files


@pytest.mark.parametrize("fail_bootstrap", [False, True])
def test_install_retires_legacy_only_after_replacements_load(tmp_path, monkeypatch, fail_bootstrap):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    legacy = target_dir / f"{installer.LABEL}.plist"
    original = plistlib.dumps({"Label": installer.LABEL, "WorkingDirectory": str(tmp_path)})
    legacy.write_bytes(original)
    generated = generated_jobs(tmp_path)
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        if command[1] == "bootstrap":
            assert legacy.exists()
            if fail_bootstrap:
                raise subprocess.CalledProcessError(5, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", run)
    if fail_bootstrap:
        with pytest.raises(subprocess.CalledProcessError):
            installer.install_jobs(tmp_path, generated, target_dir, "gui/501")
        assert legacy.read_bytes() == original
    else:
        installer.install_jobs(tmp_path, generated, target_dir, "gui/501")
        assert not legacy.exists()
        for source in generated:
            assert (target_dir / source.name).read_bytes() == source.read_bytes()
        backups = list((tmp_path / "data/scheduler/backups").glob("*.plist"))
        assert len(backups) == 1 and backups[0].read_bytes() == original
        assert calls[-1] == ["launchctl", "bootout", f"gui/501/{installer.LABEL}"]


def test_install_checks_all_owners_before_modifying_jobs(tmp_path, monkeypatch):
    target_dir = tmp_path / "LaunchAgents"
    target_dir.mkdir()
    legacy = target_dir / f"{installer.LABEL}.plist"
    original = plistlib.dumps({"WorkingDirectory": "/another/project"})
    legacy.write_bytes(original)
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="其他目录"):
        installer.install_jobs(tmp_path, generated_jobs(tmp_path), target_dir, "gui/501")
    assert not calls and legacy.read_bytes() == original
