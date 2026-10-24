"""The job queue (vidx.jobs): order, -j limit, caching, failures, logs, process trees, Windows branches."""
import io
import os
import subprocess
import sys
import time
import types

import pytest

from vidx import jobs, log
from vidx.state import OutDir

PY = [sys.executable, "-I", "-c"]


def stamp_job(tmp_path, label, seconds=0.0, code=0, output="", together=0):
    """A job that records its start/end in tmp_path/runs/<label> and exits with code. One file per job: jobs
    running together cannot share one appended file on Windows, where appends are not atomic. together=N: after
    its start it waits (up to 30 s) until N jobs have started, so the first N overlap however slowly Python starts
    (a Windows runner can take longer than a short job)."""
    (tmp_path / "runs").mkdir(exist_ok=True)
    script = (f"import os,time,sys; f=open(sys.argv[1],'a'); f.write('start {label} %f\\n' % time.time()); f.flush();"
              f"d=os.path.dirname(sys.argv[1]); t=time.time()+30\n"
              f"while len(os.listdir(d)) < {together} and time.time() < t: time.sleep(0.02)\n"
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
    q = [stamp_job(tmp_path, f"job{i}", 0.3, together=n) for i in range(5)]
    jobs.run_queue(q, n, out)
    starts = [label for kind, label, _ in runs(tmp_path) if kind == "start"]
    if n == 1:
        assert starts == [f"job{i}" for i in range(5)]  # queue order
    else:  # dispatched in queue order; the first n start together, so their processes may log in any order
        assert sorted(starts[:n]) == [f"job{i}" for i in range(n)] and sorted(starts) == sorted(set(starts))
    assert peak(tmp_path) == n


def test_more_slots_than_jobs_are_lowered_with_a_note(tmp_path, out, capsys):
    q = [stamp_job(tmp_path, f"job{i}", 0.3, together=3) for i in range(3)]
    jobs.run_queue(q, 100, out)
    assert peak(tmp_path) == 3
    err = capsys.readouterr().err
    assert "-j 100 lowered to 3: only 3 jobs to run" in err
    assert "==> running 3 jobs, 3 at a time" in err


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


def test_an_error_in_one_job_stops_the_others(tmp_path, out):
    """An unexpected exception in a -j N job (OSError: no space left, writing a stamp) went up to cli.main, which
    killed only on Ctrl-C: vidx then waited for the other jobs' threads, and their engines kept running."""
    pid_file = tmp_path / "pid"
    slow = jobs.Job("slow", str(tmp_path / "out" / "slow.json"), "p", jobs.command(
        [*PY, "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)", str(pid_file)]))

    def broken(ctx):
        deadline = time.time() + 20
        while not (pid_file.exists() and pid_file.read_text(encoding="utf-8")):
            assert time.time() < deadline
            time.sleep(0.05)
        raise OSError(28, "No space left on device")

    t0 = time.monotonic()
    with pytest.raises(OSError, match="No space left"):
        jobs.run_queue([jobs.Job("broken", "", "", broken), slow], 2, out)
    assert time.monotonic() - t0 < 20
    pid = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.time() + 5
    while alive_or_zombie(pid) and time.time() < deadline:
        time.sleep(0.05)
    assert not alive_or_zombie(pid)
    assert not (tmp_path / "out" / ".vidx" / "slow.json.params").exists()


def alive_or_zombie(pid):
    """Whether pid still runs (a killed child not yet reaped by its Popen counts as gone)."""
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_a_killed_jobs_output_is_never_stamped(tmp_path, out, capsys):
    """A job whose programs were killed (Ctrl-C, or another job's error) may leave a partial output, even with a
    body that reports success: it must not be stamped done, or the next run would take it as finished."""
    target = tmp_path / "out" / "asr.json"

    def body(ctx):
        target.write_text("{partial", encoding="utf-8", newline="\n")
        jobs.kill_all()  # what Ctrl-C does meanwhile
        return True

    jobs.run_job(jobs.Job("eng", str(target), "p", body), out, background=False)
    assert not (tmp_path / "out" / ".vidx" / "asr.json.params").exists()
    assert out.fresh(str(target), "p") and out.failed() == []
    assert "eng: done" not in capsys.readouterr().err


def test_after_kill_all_no_program_starts(monkeypatch):
    """kill_all() marks vidx as stopping: a job that comes next must not start its program (it would outlive the
    kill)."""
    monkeypatch.setattr(jobs, "spawn", lambda *a, **k: pytest.fail("a program started after kill_all"))
    jobs.kill_all()
    assert jobs.Context().call(["x"]) == 130


def test_call_returns_after_the_whole_stderr_is_shown(monkeypatch):
    """A filtered stderr is copied by a thread; call() returns only when it has copied all of it, so the end of an
    engine's output is neither lost nor shown after vidx's next message."""
    class Slow(io.StringIO):
        def write(self, text):
            time.sleep(0.005)
            return super().write(text)

    shown = Slow()
    monkeypatch.setattr(sys, "stderr", shown)
    script = "import sys; sys.stderr.write(''.join(f'line {i}\\n' for i in range(100)))"
    assert jobs.Context().call([*PY, script], drop="Fetching") == 0
    assert shown.getvalue().splitlines()[-1] == "line 99"


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


def test_stderr_to_a_file(tmp_path):
    """call(stderr=path): the program's stderr is saved (ffmpeg's showinfo log for frames.py)."""
    log_file = tmp_path / "err.log"
    script = "import sys; sys.stderr.reconfigure(newline='\\n'); sys.stderr.write('n:1 pts:2\\n')"
    assert jobs.Context().call([*PY, script], stderr=str(log_file)) == 0
    assert log_file.read_bytes() == b"n:1 pts:2\n"


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


def test_a_program_started_while_vidx_is_interrupted_is_killed(monkeypatch):
    """-j N: a worker thread inside Popen while the main thread runs kill_all() — the program is not yet in the
    list kill_all() kills, and was left running. The window is widened by killing from inside Popen."""
    started = []
    real = subprocess.Popen

    def popen_then_ctrl_c(*a, **kw):
        p = real(*a, **kw)
        started.append(p)
        jobs.kill_all()  # Ctrl-C lands now: the program exists, spawn has not registered it yet
        return p

    monkeypatch.setattr(subprocess, "Popen", popen_then_ctrl_c)
    monkeypatch.setattr(jobs, "_live", set())
    try:
        jobs.spawn([*PY, "import time; time.sleep(60)"])
        assert started[0].wait(timeout=10) is not None
    finally:
        if started and started[0].poll() is None:
            started[0].kill()
            started[0].wait()


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



def test_arguments_a_non_utf8_locale_cannot_encode_go_as_utf8(monkeypatch):
    """Linux in a Latin-1 locale: Python encodes argv in the locale's encoding, so a Cyrillic glossary word for an
    engine stopped vidx. It goes as UTF-8, in which vidx's Python children (PYTHONUTF8=1) read their argv; what the
    locale can encode (every path the OS gave vidx) is passed as Python would pass it."""
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: seen.append(argv) or FakeProc(1, []))
    monkeypatch.setattr(jobs, "_live", set())
    monkeypatch.setattr(os, "fsencode", lambda s: s.encode("latin-1", "surrogateescape"))
    jobs.spawn(["whisper", "café.wav", "--initial-prompt", "Деплой"], platform="linux")
    assert seen == [["whisper", "café.wav", "--initial-prompt", "Деплой".encode()]]


def test_text_arguments_go_as_utf8_whatever_the_locale_can_encode(monkeypatch):
    """A glossary word Latin-1 *can* encode (café) went as the Latin-1 byte for é, which a child in UTF-8 mode reads
    as an undecodable byte, not as é. Text (a prompt, a regex, a URL) goes as UTF-8 on POSIX, a path as the locale's
    bytes as before (the file name the OS gave vidx), and Windows passes Unicode either way."""
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: seen.append(argv) or FakeProc(1, []))
    monkeypatch.setattr(jobs, "_live", set())
    monkeypatch.setattr(os, "fsencode", lambda s: s.encode("latin-1", "surrogateescape"))
    for platform in ("linux", "darwin", "win32"):
        jobs.spawn(["whisper", "café.wav", "--initial-prompt", jobs.Text("café")], platform=platform)
    assert seen == [["whisper", "café.wav", "--initial-prompt", "café".encode()]] * 2 + [
        ["whisper", "café.wav", "--initial-prompt", "café"]]
    assert type(seen[2][3]) is str

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


def test_several_trees_get_their_sigterm_first_and_one_wait(monkeypatch):
    """kill_all with -j N: every tree is told to stop before vidx waits, and it waits KILL_WAIT_S once, not once
    per tree."""
    log = []
    monkeypatch.setattr(jobs, "psutil", fake_psutil(log))
    jobs.kill_trees([10, 20], platform="win32")
    assert [e for e in log if e[0] == "wait"] == [("wait", 3)]
    assert log.index(("terminate", 20)) < log.index(("wait", 3))


def test_a_second_interrupt_is_ignored(monkeypatch):
    """The handler raises Interrupted once; a Ctrl-C while vidx shuts down (kill_all) returns, so the kill
    finishes."""
    import signal

    installed = {}
    monkeypatch.setattr(signal, "signal", lambda s, h: installed.setdefault(s, h))
    monkeypatch.setattr(signal, "getsignal", lambda s: signal.SIG_DFL)
    jobs.install_interrupt_handlers()
    handler = installed[signal.SIGINT]
    with pytest.raises(jobs.Interrupted):
        handler(signal.SIGINT, None)
    assert handler(signal.SIGINT, None) is None


def test_windows_kill_tree_without_psutil_uses_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(jobs, "psutil", None)
    monkeypatch.setattr(subprocess, "run", lambda argv, **kw: calls.append(argv))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    jobs.kill_tree(42, platform="win32")
    assert calls == [[r"C:\Windows\System32\taskkill.exe", "/PID", "42", "/T", "/F"]]  # not a taskkill in the cwd


def test_interrupt_signals_per_platform():
    import signal

    if os.name != "nt":  # the terminal closed: the engines are in their own sessions and get no SIGHUP themselves
        assert jobs.interrupt_signals("linux") == jobs.interrupt_signals("darwin") == [
            signal.SIGINT, signal.SIGTERM, signal.SIGHUP]
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


def test_programs_start_only_through_jobs():
    """CLAUDE.md: every program runs through vidx.jobs (own process group, killed on Ctrl-C, waited for in slices).
    setup's device probes and nvidia-smi, and the registry's `uv tool dir`, ran with subprocess.run instead."""
    import ast

    from helpers import ROOT

    sources = [p for p in sorted((ROOT / "src" / "vidx").glob("*.py")) if p.name != "jobs.py"]
    found = [f"{path.name}:{node.lineno}" for path in sources
             for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
             and node.func.attr in ("run", "Popen", "call", "check_call", "check_output")]
    assert found == []


def test_output_reads_both_streams_and_kills_a_program_past_its_timeout(tmp_path):
    # bytes, not text: Windows would write CRLF, a Latin-1 locale (-I ignores PYTHONUTF8) Latin-1
    script = "import sys; sys.stdout.buffer.write(b'caf\\xc3\\xa9\\n'); sys.stderr.buffer.write(b'w\\n'); sys.exit(3)"
    assert jobs.output([*PY, script]) == (3, "café\n", "w\n")
    pid_file, before = tmp_path / "pid", set(jobs._live)
    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        jobs.output([*PY, "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(60)",
                     str(pid_file)], timeout=2)
    assert time.monotonic() - t0 < 15
    assert not alive_or_zombie(int(pid_file.read_text(encoding="utf-8")))
    assert set(jobs._live) <= before  # unregistered


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
        def read1(self, size=-1):
            readers.append(threading.current_thread() is threading.main_thread())
            return super().read1(size)

    class Proc:
        pid, stderr = 1, Lines(b"Fetching 4 files\nreal warning\n")

        def wait(self, timeout=None):
            assert timeout is not None
            return 0

    monkeypatch.setattr(jobs, "spawn", lambda argv, **kw: Proc())
    shown = io.StringIO()
    assert jobs.Context(shown).call(["x"], drop="Fetching") == 0
    assert readers and not any(readers) and shown.getvalue() == "real warning\n"


# --- engine stderr on a terminal ------------------------------------------------------------------------------
class Terminal(io.TextIOWrapper):
    """A stand-in terminal: its own encoding, set up by log.setup_streams as vidx sets up its stderr."""

    def __init__(self, encoding):
        super().__init__(io.BytesIO(), encoding=encoding, newline="\n")
        log.setup_streams([self])

    def isatty(self):
        return True

    def shown(self):
        self.flush()
        return self.buffer.getvalue()


# LF on Windows too, as a lib script's redirected stderr (common.unix_stderr); the relay passes line ends on as they are
CHILD = [sys.executable, "-I", "-X", "utf8", "-c",
         "import sys; sys.stderr.reconfigure(newline='\\n'); sys.stderr.write('caf\\xe9 \\u2192 ok\\n')"]


def test_engine_stderr_is_reencoded_for_a_non_utf8_terminal(monkeypatch):
    """vidx's Python children write UTF-8, which a Latin-1 terminal shows as two wrong letters per é. With -j 1 their
    stderr now goes through vidx, which writes it in the terminal's encoding, "?" for what it has no code for."""
    tty = Terminal("latin-1")
    monkeypatch.setattr(sys, "stderr", tty)
    assert jobs.Context().call(CHILD) == 0
    assert tty.shown() == "café ? ok\n".encode("latin-1")


def test_captured_programs_stderr_is_reencoded_too(monkeypatch):
    tty = Terminal("latin-1")
    monkeypatch.setattr(sys, "stderr", tty)
    assert jobs.capture(CHILD) == (0, "")
    assert tty.shown() == "café ? ok\n".encode("latin-1")


@pytest.mark.parametrize("encoding", ["utf-8", None])
def test_a_utf8_terminal_or_a_file_gets_the_programs_stderr_directly(monkeypatch, encoding):
    """Only a non-UTF-8 terminal needs the relay: elsewhere the program keeps writing to vidx's stderr itself, so
    it still sees a terminal (colours, progress bars) and nothing changes."""
    seen = {}
    monkeypatch.setattr(sys, "stderr", Terminal(encoding) if encoding else io.StringIO())
    class Done:
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(jobs, "spawn", lambda argv, **kw: seen.update(kw) or Done())
    assert jobs.Context().call(["x"]) == 0
    assert seen["stderr"] is None


def test_relay_passes_progress_lines_as_they_come_and_reads_non_utf8_as_the_locale():
    """A piece ends at \\r or \\n, so "\\r  whisper 2/9 s" is shown while the engine runs, not when it ends; a piece
    that is not UTF-8 (ffmpeg naming a Latin-1 file) is read in the locale's encoding."""
    class Chunks:
        def __init__(self, *chunks):
            self.chunks = list(chunks)

        def read1(self, size=-1):
            return self.chunks.pop(0) if self.chunks else b""

    class Dest:
        writes = []

        def write(self, text):
            self.writes.append(text)

        def flush(self):
            pass

    jobs.relay(Chunks(b"\r  whisper 1/9", b" s\r  whisper 2/9 s", b"\n", b"caf\xe9.wav: Fetching\n", "Größe".encode()),
               Dest(), drop="Fetching", fallback="latin-1")
    assert Dest.writes == ["\r", "  whisper 1/9 s\r", "  whisper 2/9 s\n", "Größe"]


@pytest.mark.skipif(os.name == "nt", reason="a pseudo-terminal (POSIX)")
def test_engine_stderr_on_a_real_latin1_terminal(tmp_path):
    """The same through a real pseudo-terminal in a real Latin-1 locale (macOS; ci latin-1 on Linux)."""
    import pty

    from test_text_io import latin1_env

    if not latin1_env():
        pytest.skip("needs a Latin-1 locale")
    driver = tmp_path / "driver.py"
    driver.write_text("import sys\nfrom vidx import jobs, log\nlog.setup_streams()\n"
                      f"sys.exit(jobs.Context().call({CHILD!r}))\n", encoding="utf-8", newline="\n")
    master, slave = pty.openpty()
    p = subprocess.Popen([sys.executable, str(driver)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=slave, env=latin1_env())
    os.close(slave)
    shown = b""
    while True:
        try:
            data = os.read(master, 4096)
        except OSError:  # Linux: EIO once the driver has exited
            break
        if not data:
            break
        shown += data
    os.close(master)
    assert p.wait(timeout=30) == 0
    assert b"caf\xe9 ? ok" in shown, shown


def test_a_temp_folder_outlives_an_interrupt_until_the_programs_are_killed(tmp_path, monkeypatch):
    """Windows, -j 1: Ctrl-C in the frames step unwound the step (rmtree of ffmpeg's temp folder) before cli.main
    killed ffmpeg, which still held files there, so the folder was left behind; with -j N vidx could exit before the
    worker thread removed it. The folder is now removed after kill_all (remove_temps), not while unwinding."""
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with jobs.temp_dir() as d:
        assert os.path.isdir(d)
    assert not os.path.exists(d)  # a normal end: gone at once
    with pytest.raises(jobs.Interrupted), jobs.temp_dir() as d:
        jobs._interrupted.set()  # what the signal handler does before it raises
        raise jobs.Interrupted
    assert os.path.isdir(d)  # a program may still write into it
    jobs.remove_temps()
    assert not os.path.exists(d)


# --- cli.main's way out ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("exc, code", [(jobs.Interrupted(), 130), (OSError(28, "No space left on device"), 1)])
def test_cli_kills_every_tree_on_any_exception(monkeypatch, capsys, exc, code):
    """Ctrl-C and an unexpected error leave the same way: every program tree killed, then exit (130 or 1) without
    waiting for worker threads."""
    from vidx import cli

    calls = []

    def boom(args):
        raise exc

    class Exit(Exception):
        pass

    def fake_exit(c):
        calls.append(("exit", c))
        raise Exit

    monkeypatch.setattr(cli, "dispatch", boom)
    monkeypatch.setattr(jobs, "kill_all", lambda: calls.append(("kill_all",)))
    monkeypatch.setattr(jobs, "remove_temps", lambda: calls.append(("remove_temps",)))
    monkeypatch.setattr(jobs, "install_interrupt_handlers", lambda: None)
    monkeypatch.setattr(os, "_exit", fake_exit)
    with pytest.raises(Exit):
        cli.main(["x"])
    assert calls == [("kill_all",), ("remove_temps",), ("exit", code)]
    err = capsys.readouterr().err
    assert ("interrupted" in err) if code == 130 else ("No space left on device" in err and "unexpected error" in err)
