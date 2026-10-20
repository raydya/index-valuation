"""Bounded, noninteractive Codex execution for connector-backed host jobs."""

import os
import signal
import subprocess
from pathlib import Path


class _Interrupted(BaseException):
    def __init__(self, signum):
        self.signum = signum


def _stop(child):
    if child is None:
        return
    previous = {}
    try:
        # A repeated stop signal must not leave cleanup half done.
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, signal.SIG_IGN)
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        # wait() only observes the group leader. Its descendants may ignore TERM
        # and keep running after the leader exits, so always clear the group.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def execute_codex(*, root, codex, prompt, run_dir, schema, lock_fd=None, timeout=3600):
    """Run a fresh session; process exit never asserts that a sheet was updated."""
    output = Path(run_dir) / "result.json"
    command = [
        str(codex),
        "--no-daemon",
        "exec",
        "--approve-for-me",
        "-C",
        str(root),
        "--json",
        "--output-schema",
        str(schema),
        "--output-last-message",
        str(output),
        "-",
    ]
    child = None
    previous = {}

    def interrupted(signum, _frame):
        raise _Interrupted(signum)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, interrupted)
        with (
            (Path(run_dir) / "events.jsonl").open("wb") as events,
            (Path(run_dir) / "stderr.log").open("wb") as errors,
        ):
            child = subprocess.Popen(
                command,
                cwd=root,
                stdin=subprocess.PIPE,
                stdout=events,
                stderr=errors,
                start_new_session=True,
                pass_fds=() if lock_fd is None else (lock_fd,),
            )
            child.communicate(input=prompt.encode(), timeout=timeout)
        return {
            "status": "finished" if child.returncode == 0 else "failed",
            "exit_code": child.returncode,
        }
    except subprocess.TimeoutExpired:
        _stop(child)
        return {"status": "timed_out", "exit_code": 124}
    except (_Interrupted, KeyboardInterrupt) as error:
        # Prevent another signal from interrupting process-group cleanup.
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        _stop(child)
        return {"status": "interrupted", "exit_code": 128 + getattr(error, "signum", signal.SIGINT)}
    except Exception:
        # Unexpected pipe/runtime errors also need bounded process-group cleanup.
        _stop(child)
        return {"status": "failed", "exit_code": 1}
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
