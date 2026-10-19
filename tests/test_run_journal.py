import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from index_valuation import cli, run_journal
from index_valuation.domain import DataError, IndexSpec, SourceError
from index_valuation.run_journal import ResultJournal, read_reports


def test_journal_persists_each_complete_result_and_freezes_values(tmp_path, monkeypatch):
    path, batch_id = tmp_path / "batches" / "result.jsonl", uuid4()
    syncs = []
    monkeypatch.setattr(run_journal.os, "fsync", lambda descriptor: syncs.append(descriptor))
    report = {
        "index": "csi300",
        "latest": {"pe": Decimal("12.34"), "trade_date": date(2026, 10, 19)},
    }
    with ResultJournal(path, batch_id) as journal:
        journal.append(report)
        assert len(syncs) == 1
        assert read_reports(path, batch_id) == [
            {"index": "csi300", "latest": {"pe": "12.34", "trade_date": "2026-10-19"}}
        ]
        report["latest"]["pe"] = Decimal("99")
        journal.append({"index": "paused", "status": "blocked"})
        assert len(syncs) == 2
    records = read_reports(path, batch_id)
    assert len(path.read_bytes().splitlines()) == len(records) == 2
    assert records[0]["latest"]["pe"] == "12.34"
    assert records[1] == {"index": "paused", "status": "blocked"}


def test_journal_never_overwrites_an_existing_batch(tmp_path):
    path, batch_id = tmp_path / "result.jsonl", uuid4()
    with ResultJournal(path, batch_id) as journal:
        journal.append({"index": "csi300"})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        ResultJournal(path, uuid4())
    assert path.read_bytes() == original


@pytest.mark.parametrize("fragment", [b'{"batch_id":', b'{"report":"\xe4', b"{}"])
def test_reader_ignores_unterminated_tail_and_other_batches(tmp_path, fragment):
    path, batch_id = tmp_path / "result.jsonl", uuid4()
    with ResultJournal(path, uuid4()) as journal:
        journal.append({"index": "other_batch"})
    with path.open("ab") as output:
        output.write(
            json.dumps({"batch_id": str(batch_id), "report": {"index": "csi300"}}).encode()
            + b"\n"
            + fragment
        )
    assert read_reports(path, batch_id) == [{"index": "csi300"}]


@pytest.mark.parametrize("line", [b"{invalid}\n", b"[]\n", b'"\xff"\n'])
def test_reader_rejects_corrupt_complete_lines(tmp_path, line):
    path = tmp_path / "result.jsonl"
    path.write_bytes(line)
    with pytest.raises(DataError, match="第 1 行"):
        read_reports(path, uuid4())


def test_reader_rejects_invalid_matched_report_but_missing_file_is_empty(tmp_path):
    path, batch_id = tmp_path / "result.jsonl", uuid4()
    assert read_reports(path, batch_id) == []
    path.write_text(json.dumps({"batch_id": str(batch_id), "report": []}) + "\n")
    with pytest.raises(DataError, match="缺少有效指数结果"):
        read_reports(path, batch_id)


@pytest.fixture
def cli_environment(tmp_path, monkeypatch):
    settings = SimpleNamespace(indices_file=tmp_path / "indices.toml", data_dir=tmp_path)
    settings.source_timeout = 1
    first = IndexSpec(
        "first",
        "First",
        "000300",
        "SH.000300",
        inception_date=date(2026, 1, 1),
        reference_history_source="",
        futu_supported=False,
    )
    specs = [first, replace(first, key="second"), replace(first, key="paused")]
    specs[-1] = replace(specs[-1], collection_enabled=False, collection_disabled_reason="paused")
    refreshed, finished = [], []
    repository = SimpleNamespace(
        index_series=lambda *_: [],
        ensure_index=lambda *_: None,
        lock=lambda *_: nullcontext(),
        start_run=lambda *_: uuid4(),
        finish_run=lambda run_id, status, report: finished.append(report),
    )
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "load_indices", lambda _: {spec.key: spec for spec in specs})
    monkeypatch.setattr(cli, "build_engine", lambda _: SimpleNamespace(dispose=lambda: None))
    monkeypatch.setattr(cli, "Repository", lambda _: repository)
    monkeypatch.setattr(cli, "Pipeline", lambda *_: None)
    monkeypatch.setattr(cli, "TotalReturnSources", lambda *_: None)
    monkeypatch.setattr(cli, "TotalReturnCollector", lambda *_: None)
    monkeypatch.setattr(cli, "FutuRestClient", lambda **_: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(
        cli,
        "HistoricalBackfill",
        lambda *_args, **_kwargs: SimpleNamespace(
            run=lambda *_args, **_kwargs: {"status": "collected", "sources": {}}
        ),
    )

    def refresh(spec):
        refreshed.append(spec.key)
        path = tmp_path / f"{spec.key}.json"
        return {
            "index": spec.key,
            "status": "collected",
            "run_id": str(uuid4()),
            "latest": {"pe": Decimal("12.34")},
            "report_path": str(path),
        }

    monkeypatch.setattr(cli, "IndexSeries", lambda *_: SimpleNamespace(refresh=refresh))
    return SimpleNamespace(refreshed=refreshed, finished=finished, refresh=refresh)


def test_cli_journals_final_results_including_paused_without_changing_stdout(
    tmp_path, cli_environment, capsys
):
    path, batch_id = tmp_path / "result.jsonl", uuid4()
    assert (
        cli.main(
            ["update-index", "--all", "--result-journal", str(path), "--batch-id", str(batch_id)]
        )
        == 2
    )
    records = read_reports(path, batch_id)
    assert [record["index"] for record in records] == ["first", "second", "paused"]
    assert records[0]["collection"]["status"] == "collected"
    assert records[-1]["status"] == "blocked"
    assert records == json.loads(capsys.readouterr().out)


def test_cli_preserves_completed_index_when_next_index_fails(
    tmp_path, cli_environment, monkeypatch
):
    path, batch_id = tmp_path / "result.jsonl", uuid4()

    def refresh(spec):
        if spec.key == "second":
            raise SourceError("unavailable")
        return cli_environment.refresh(spec)

    monkeypatch.setattr(cli, "IndexSeries", lambda *_: SimpleNamespace(refresh=refresh))
    assert (
        cli.main(
            ["update-index", "--all", "--result-journal", str(path), "--batch-id", str(batch_id)]
        )
        == 1
    )
    assert [record["index"] for record in read_reports(path, batch_id)] == ["first"]


def test_cli_stops_on_journal_sync_failure(tmp_path, cli_environment, monkeypatch):
    def fail(_descriptor):
        raise OSError("disk unavailable")

    monkeypatch.setattr(run_journal.os, "fsync", fail)
    assert (
        cli.main(
            [
                "update-index",
                "--all",
                "--result-journal",
                str(tmp_path / "results.jsonl"),
                "--batch-id",
                str(uuid4()),
            ]
        )
        == 1
    )
    assert cli_environment.refreshed == ["first"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["update-index", "--all", "--result-journal", "results.jsonl"],
        ["update-index", "--all", "--batch-id", str(uuid4())],
        ["update-index", "--all", "--result-journal", "results.jsonl", "--batch-id", "bad"],
        ["backfill-index", "--all", "--result-journal", "results.jsonl"],
    ],
)
def test_journal_options_require_a_pair_and_are_only_for_updates(arguments):
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
