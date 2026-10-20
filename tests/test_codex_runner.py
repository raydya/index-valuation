import signal
import subprocess
from types import SimpleNamespace

import pytest

from index_valuation import codex_runner


def test_cleanup_kills_a_descendant_that_outlives_the_exited_group_leader(monkeypatch):
    descendant = {"alive": True}
    signals = []

    def killpg(pid, signum):
        assert pid == 12345
        signals.append(signum)
        # The group leader exits on TERM, but its descendant ignores TERM.
        if signum == signal.SIGKILL:
            descendant["alive"] = False

    child = SimpleNamespace(pid=12345, wait=lambda timeout: 0)
    monkeypatch.setattr(codex_runner.os, "killpg", killpg)
    codex_runner._stop(child)
    assert descendant["alive"] is False
    assert signals[0] == signal.SIGTERM


def test_cleanup_reaps_the_child_when_its_process_group_has_already_gone(monkeypatch):
    waits = []

    def gone(*_args):
        raise ProcessLookupError

    child = SimpleNamespace(pid=12345, wait=lambda timeout: waits.append(timeout))
    monkeypatch.setattr(codex_runner.os, "killpg", gone)
    codex_runner._stop(child)
    assert waits, "An already exited child must still be reaped"


def test_cleanup_escalates_when_the_group_leader_ignores_term(monkeypatch):
    waits, signals = [], []

    def wait(timeout):
        waits.append(timeout)
        if len(waits) == 1:
            raise subprocess.TimeoutExpired("codex", timeout)
        return -signal.SIGKILL

    child = SimpleNamespace(pid=12345, wait=wait)
    monkeypatch.setattr(codex_runner.os, "killpg", lambda _pid, signum: signals.append(signum))
    codex_runner._stop(child)
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert waits == [10, 5]


def test_cleanup_cannot_be_interrupted_by_a_second_termination_signal(monkeypatch):
    original = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}

    def killpg(_pid, _signum):
        assert signal.getsignal(signal.SIGTERM) == signal.SIG_IGN
        assert signal.getsignal(signal.SIGINT) == signal.SIG_IGN

    monkeypatch.setattr(codex_runner.os, "killpg", killpg)
    codex_runner._stop(SimpleNamespace(pid=12345, wait=lambda timeout: 0))
    assert {signum: signal.getsignal(signum) for signum in original} == original


@pytest.mark.parametrize("problem", [OSError("pipe failed"), RuntimeError("unexpected pipe error")])
def test_execution_failure_always_stops_an_already_started_process_group(
    tmp_path, monkeypatch, problem
):
    signals = []

    class Child:
        pid = 12345
        returncode = None

        def __init__(self, *_args, **_kwargs):
            pass

        def communicate(self, **_kwargs):
            raise problem

        def wait(self, timeout):
            self.returncode = -signal.SIGTERM
            return self.returncode

    monkeypatch.setattr(codex_runner.subprocess, "Popen", Child)
    monkeypatch.setattr(codex_runner.os, "killpg", lambda _pid, signum: signals.append(signum))
    result = codex_runner.execute_codex(
        root=tmp_path,
        codex="/not-executed",
        prompt="Test only; no real task.",
        run_dir=tmp_path,
        schema=tmp_path / "unused-schema.json",
    )
    assert result == {"status": "failed", "exit_code": 1}
    assert signals == [signal.SIGTERM, signal.SIGKILL]
