"""The job queue (vidx.jobs): order, -j limit, caching, failures, logs, process trees, Windows branches."""
import os
import subprocess
import sys
import time
import types

import pytest

from vidx import jobs
from vidx.state import OutDir

PY = [sys.executable, "-I", "-c"]


def stamp_job(tmp_path, label, seconds=0.0, code=0, output=""):
    """A job that records its start/end in tmp_path/runs/<label> and exits with code. One file per job: jobs
    running together cannot share one appended file on Windows, where appends are not atomic."""
    (tmp_path / "runs").mkdir(exist_ok=True)
    script = (f"import time,sys; f=open(sys.argv[1],'a'); f.write('start {label} %f\\n' % time.time()); f.flush();"
              f"time.sleep({seconds}); f.write('end {label} %f\\n' % time.time()); sys.exit({code})")
    return jobs.Job(label, output, "p", jobs.command([*PY, script, str(tmp_path / "runs" / label)]))


def runs(tmp_path):
    """[(start|end, label, time)] of all stamp jobs, in time order."""
    rows = [line.split() for f in (tmp_path / "runs").iterdir() for line in f.read_text(encoding="utf-8").splitlines()]
    return sorted(rows, key=lambda row: float(row[2]))


def peak(tmp_path):
    rows = runs(tmp_path)
    spans = {}
    for kind, label, t in rows:
        spans.setdefault(label, {})[kind] = float(t)
    return max(sum(s["start"] <= x["start"] < s["end"] for s in spans.values()) for x in spans.values())


@pytest.fixture
def out(tmp_path):
    o = OutDir.prepare(str(tmp_path / "out"), force=False)
    o.reset_failed()
    return o


@pytest.mark.parametrize("n", [1, 2, 3])
def test_limit_and_order(tmp_path, out, n):
    q = [stamp_job(tmp_path, f"job{i}", 0.6) for i in range(5)]
    jobs.run_queue(q, n, out)
    starts = [label for kind, label, _ in runs(tmp_path) if kind == "start"]
    if n == 1:
        assert starts == [f"job{i}" for i in range(5)]  # queue order
    else:  # dispatched in queue order; the first n start together, so their processes may log in any order
        assert sorted(starts[:n]) == [f"job{i}" for i in range(n)] and sorted(starts) == sorted(set(starts))
    assert peak(tmp_path) == n


def test_failure_does_not_stop_the_rest(tmp_path, out, capsys):
    q = [stamp_job(tmp_path, "a"), stamp_job(tmp_path, "bad", code=1), stamp_job(tmp_path, "c")]
    jobs.run_queue(q, 2, out)
    assert sorted(label for kind, label, _ in runs(tmp_path) if kind == "end") == ["a", "bad", "c"]
    assert out.failed() == ["bad"]
    err = capsys.readouterr().err
    assert "warn: bad failed, continuing" in err
    assert "==> running 3 jobs, 2 at a time" in err
    assert (tmp_path / "out" / "logs" / "bad.log").exists()


def test_failed_job_log_tail_shown(tmp_path, out, capsys):
    script = "import sys; print('\\n'.join(f'line {i}' for i in range(9))); sys.exit(2)"
    jobs.run_queue([jobs.Job("noisy", "", "", jobs.command([*PY, script])), stamp_job(tmp_path, "x")], 2, out)
    err = capsys.readouterr().err
    assert "    line 4\n    line 5\n    line 6\n    line 7\n    line 8\n" in err and "line 3" not in err


def test_cached_and_stamped(tmp_path, out, capsys):
    target = tmp_path / "out" / "asr.json"
    script = "import sys; open(sys.argv[1], 'w', encoding='utf-8', newline='\\n').write('{}')"
    job = jobs.Job("eng [ru]", str(target), "model ru  ", jobs.command([*PY, script, str(target)]))
    jobs.run_queue([job], 1, out)
    assert (tmp_path / "out" / ".vidx" / "asr.json.params").read_text(encoding="utf-8") == "model ru  "
    jobs.run_queue([job], 1, out)
    err = capsys.readouterr().err
    assert "==> eng [ru]\n" in err and "==> eng [ru]: done in" in err and "==> eng [ru]: cached" in err


@pytest.mark.parametrize("label, name", [("GigaAM-v3 [ru]", "GigaAM-v3_ru_.log"), ("frames + OCR", "frames_OCR.log"),
                                         ("whisper large-v3 [en]", "whisper_large-v3_en_.log"),
                                         ("Vosk [ru] (CPU)", "Vosk_ru_CPU_.log"), ("Видео", "_.log")])
def test_log_names_like_bash(label, name):
    assert jobs.log_name(label) == name


def test_python_job_and_stdout_to_file(tmp_path, out):
    target = tmp_path / "out" / "lang.tmp"

    def body(ctx):
        ctx.say("frames: hello")
        # call() saves the bytes as printed; LF on Windows is the script's job, as in lib/ (common.unix_stdout)
        return ctx.call([*PY, "import sys; sys.stdout.reconfigure(newline='\\n'); print('ru,en')"],
                        stdout=str(target)) == 0

    jobs.run_queue([jobs.Job("py", "", "", body), stamp_job(tmp_path, "x")], 2, out)
    assert target.read_bytes() == b"ru,en\n"
    assert "frames: hello" in (tmp_path / "out" / "logs" / "py.log").read_text(encoding="utf-8")


def test_stderr_filter(tmp_path, out, capsys):
    script = "import sys; sys.stderr.write('Fetching 4 files\\nreal warning\\n')"
    jobs.run_queue([jobs.Job("lid", "", "", lambda ctx: ctx.call([*PY, script], drop="Fetching") == 0)], 1, out)
    err = capsys.readouterr().err
    assert "real warning" in err and "Fetching" not in err


def test_children_get_utf8_python_and_no_stdin(tmp_path, out):
    target = tmp_path / "env.txt"
    script = "import os,sys; print(os.environ.get('PYTHONUTF8'), sys.stdin.read() == '')"
    jobs.run_queue([jobs.Job("e", "", "", lambda ctx: ctx.call([*PY, script], stdout=str(target)) == 0)], 1, out)
    assert target.read_text(encoding="utf-8").split() == ["1", "True"]


# --- process trees -------------------------------------------------------------------------------------------
TREE = """
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
time.sleep(60)
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("with_psutil", [True, False])
def test_kill_tree_kills_grandchildren(tmp_path, monkeypatch, with_psutil):
    if not with_psutil:
        monkeypatch.setattr(jobs, "psutil", None)
    pid_file = tmp_path / "grandchild"
    p = jobs.spawn([*PY, TREE, str(pid_file)])
    deadline = time.time() + 20
    while not pid_file.exists() or not pid_file.read_text(encoding="utf-8"):
        assert time.time() < deadline
        time.sleep(0.05)
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    jobs.kill_tree(p.pid)
    p.wait(timeout=5)  # (psutil may have reaped it already: then Popen reports 0)
    deadline = time.time() + 5
    while alive(grandchild) and time.time() < deadline:
        time.sleep(0.05)
    assert not alive(grandchild)


def test_spawn_flags_per_platform(monkeypatch):
    seen = {}

    def fake_popen(argv, **kw):
        seen.update(kw)
        return FakeProc(1, [])

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(jobs, "_live", set())  # the fakes must not stay registered
    jobs.spawn(["x"], platform="win32")
    assert seen["creationflags"] == 0x200 and "start_new_session" not in seen  # CREATE_NEW_PROCESS_GROUP
    seen.clear()
    jobs.spawn(["x"], platform="linux")
    assert seen["start_new_session"] is True and not seen.get("creationflags")
    assert seen["stdin"] == subprocess.DEVNULL and seen["env"]["PYTHONUTF8"] == "1"


class FakeProc:
    def __init__(self, pid, log, survives=False):
        self.pid, self.log, self.survives = pid, log, survives

    def terminate(self):
        self.log.append(("terminate", self.pid))

    def kill(self):
        self.log.append(("kill", self.pid))

    def children(self, recursive=False):
        assert recursive
        return [FakeProc(11, self.log), FakeProc(12, self.log, survives=True)]


def fake_psutil(log):
    def wait_procs(procs, timeout):
        log.append(("wait", timeout))
        return [p for p in procs if not p.survives], [p for p in procs if p.survives]

    return types.SimpleNamespace(Process=lambda pid: FakeProc(pid, log), wait_procs=wait_procs,
                                 NoSuchProcess=ProcessLookupError, AccessDenied=PermissionError)


def test_windows_kill_tree_with_psutil(monkeypatch):
    log = []
    monkeypatch.setattr(jobs, "psutil", fake_psutil(log))
    monkeypatch.setattr(os, "killpg", lambda *a: log.append(("killpg",) + a), raising=False)
    jobs.kill_tree(10, platform="win32")
    assert log == [("terminate", 11), ("terminate", 12), ("terminate", 10), ("wait", 3), ("kill", 12)]


def test_windows_kill_tree_without_psutil_uses_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "psutil", None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv))
    jobs.kill_tree(42, platform="win32")
    assert calls == [["taskkill", "/PID", "42", "/T", "/F"]]


def test_interrupt_signals_per_platform():
    import signal

    assert jobs.interrupt_signals("linux") == [signal.SIGINT, signal.SIGTERM]
    names = [getattr(s, "name", s) for s in jobs.interrupt_signals("win32")]
    assert names[:2] == ["SIGINT", "SIGTERM"] and names[2] in ("SIGBREAK", 21)


# --- waits the Ctrl-C / Ctrl-Break handler can interrupt ------------------------------------------------------
def blocking_waits(path):
    """wait() / communicate() calls without a timeout: on Windows they block in WaitForSingleObject, and the
    main thread's interrupt handler runs only when the program has ended (with -j 1, after a whole engine)."""
    import ast

    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                "wait", "communicate") and not node.args and "timeout" not in {k.arg for k in node.keywords}:
            yield f"{path.name}:{node.lineno}: {node.func.attr}() without a timeout"


def test_the_orchestrator_waits_in_slices():
    from helpers import ROOT

    found = [p for path in sorted((ROOT / "src" / "vidx").glob("*.py")) for p in blocking_waits(path)]
    assert found == []


def test_wait_returns_the_exit_status_after_slices():
    slices = []

    class Slow:
        def wait(self, timeout=None):
            slices.append(timeout)
            if len(slices) < 3:
                raise subprocess.TimeoutExpired("x", timeout)
            return 7

    assert jobs.wait(Slow()) == 7
    assert slices == [jobs.POLL_S] * 3


def test_dropped_stderr_is_read_off_the_main_thread(monkeypatch):
    """call(drop=...) filters the program's stderr in a thread, so the main thread only waits (in slices)."""
    import io
    import threading

    readers = []

    class Lines(io.BytesIO):
        def __iter__(self):
            readers.append(threading.current_thread() is threading.main_thread())
            return super().__iter__()

    class Proc:
        pid, stderr = 1, Lines(b"Fetching 4 files\nreal warning\n")

        def wait(self, timeout=None):
            assert timeout is not None
            return 0

    monkeypatch.setattr(jobs, "spawn", lambda argv, **kw: Proc())
    shown = io.StringIO()
    assert jobs.Context(shown).call(["x"], drop="Fetching") == 0
    assert readers == [False] and shown.getvalue() == "real warning\n"
