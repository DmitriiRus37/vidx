"""The job queue: engines and frames+OCR, one by one or -j N at a time; process trees and Ctrl-C.

Every external program runs through spawn(): its own process group (POSIX: a new session; Windows:
CREATE_NEW_PROCESS_GROUP), stdin from the null device, PYTHONUTF8=1 for Python children. The terminal's
Ctrl-C then reaches only vidx, which kills every running tree (kill_tree: psutil's view of the
descendants, plus killpg on POSIX, or taskkill on Windows when psutil is missing) and exits 130. The main
thread waits for programs in POLL_S slices (wait, communicate): Windows runs the handler only between them.
"""
import codecs
import contextlib
import locale
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from dataclasses import dataclass

from vidx import paths
from vidx.log import log, warn

try:
    import psutil
except ImportError:  # optional on POSIX (killpg covers the tree); a dependency of the package
    psutil = None

KILL_WAIT_S = 3
POLL_S = 0.5  # a wait without a timeout blocks a Windows Ctrl-C / Ctrl-Break until the program ends
_live = set()  # running Popen objects
_live_lock = threading.Lock()
_interrupted = threading.Event()


class Interrupted(BaseException):
    """Ctrl-C, SIGTERM, SIGHUP (POSIX) or Ctrl-Break: kill everything, exit 130."""


@dataclass
class Job:
    label: str
    output: str  # "" = the job checks its own cache
    params: str
    run: Callable  # run(ctx) → True if it succeeded


def command(argv, **kw):
    """A job body that runs one program."""
    return lambda ctx: ctx.call(argv, **kw) == 0


def child_env(extra=None):
    env = {**os.environ, "PYTHONUTF8": "1"}  # Python children read and print UTF-8 on every OS
    env.update(extra or {})
    return env


class Text(str):
    """An argument that is text, not a file name: the whisper prompt, the find regex, a URL. See os_arg."""
    __slots__ = ()


def os_arg(a, platform=None):
    """An argument as the program gets it. POSIX passes bytes, and Python encodes a str in the locale's encoding, while
    vidx's Python children (PYTHONUTF8=1, or -X utf8) and yt-dlp read their argv as UTF-8. So in a non-UTF-8 locale
    (Latin-1 on Linux) text would arrive wrong: a Cyrillic glossary word cannot be encoded at all, and the é of café
    becomes a byte the child cannot decode. Text therefore always goes as UTF-8, and so does any argument the locale
    cannot encode; a path the locale can encode (every file name the OS gave vidx) keeps the locale's bytes, as before.
    macOS encodes argv in UTF-8 whatever the locale, and Windows passes Unicode."""
    if isinstance(a, Text):
        return str(a) if (platform or paths.platform_of()) == "win32" else a.encode("utf-8", "surrogateescape")
    a = str(a)
    try:
        os.fsencode(a)
        return a
    except UnicodeEncodeError:
        return a.encode("utf-8", "surrogateescape")


def spawn(argv, platform=None, stdout=None, stderr=None, env=None):
    """Start a program in its own process group, registered for kill_all()."""
    platform = platform or paths.platform_of()
    kw = dict(stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=child_env(env))
    if platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen([os_arg(a, platform) for a in argv], **kw)
    with _live_lock:  # kill_all sets _interrupted, then lists _live under this lock: p is in its list, or seen here
        _live.add(p)
        late = _interrupted.is_set()
    if late:  # started while vidx was being interrupted (another thread, -j N): kill_all may not have seen it
        kill_tree(p.pid, platform)
    return p


def wait(p):
    """p.wait(), in POLL_S slices so that an interrupt is handled while a program runs."""
    while True:
        try:
            return p.wait(timeout=POLL_S)
        except subprocess.TimeoutExpired:
            continue


def communicate(p):
    """p.communicate() → (stdout, stderr), in POLL_S slices (a retry loses no output)."""
    while True:
        try:
            return p.communicate(timeout=POLL_S)
        except subprocess.TimeoutExpired:
            continue


def reencodes(stream):
    """Whether a program's stderr must go through vidx to reach this stream: a terminal that is not UTF-8 (a Latin-1
    locale on Linux or macOS), where the UTF-8 that vidx's Python children write would show garbled. Anywhere else
    (a file, a pipe, a UTF-8 terminal, the Windows console) the program writes to vidx's stderr itself and still sees
    a terminal where there is one (colours, progress bars)."""
    try:
        return stream.isatty() and codecs.lookup(stream.encoding).name != "utf-8"
    except (AttributeError, LookupError, OSError, TypeError, ValueError):
        return False


def as_text(piece, fallback):
    """Program output → text: UTF-8 (vidx's Python children, the Swift helpers), else, for a non-Python program
    in a non-UTF-8 locale (ffmpeg naming a Latin-1 file), the locale's encoding."""
    try:
        return piece.decode("utf-8")
    except UnicodeDecodeError:
        return piece.decode(fallback, "replace")


def relay(stream, dest, drop=None, fallback=None):
    """Copy a program's stderr to the text stream dest as it comes, piece by piece: a piece ends at \\n or \\r, so a
    progress line shows while it is drawn, and a piece containing drop is left out. dest writes the text in its own
    encoding: a terminal's with "?" for what it lacks (log.setup_streams), a log file UTF-8."""
    fallback = fallback or locale.getpreferredencoding(False)
    rest = b""
    while chunk := stream.read1(1 << 16):
        *pieces, rest = re.split(rb"(?<=[\r\n])", rest + chunk)
        for piece in pieces:
            _show(as_text(piece, fallback), dest, drop)
    if rest:
        _show(as_text(rest, fallback), dest, drop)


def _show(text, dest, drop):
    if text and not (drop and drop in text):
        dest.write(text)
        dest.flush()


def _forget(p):
    with _live_lock:
        _live.discard(p)


def kill_tree(pid, platform=None):
    """Terminate a process and all its descendants; kill what is still alive after KILL_WAIT_S."""
    kill_trees([pid], platform)


def kill_trees(pids, platform=None):
    """kill_tree for several trees at once: every tree gets its SIGTERM first, then one wait of KILL_WAIT_S for
    all of them, then a kill for what is left (not KILL_WAIT_S per tree)."""
    platform = platform or paths.platform_of()
    procs = []
    if psutil:
        for pid in pids:
            try:
                parent = psutil.Process(pid)
                procs += [*parent.children(recursive=True), parent]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    if platform != "win32":
        for pid in pids:
            with contextlib.suppress(OSError):  # ProcessLookupError, PermissionError
                os.killpg(pid, signal.SIGTERM)  # the job's own process group (new session)
    if psutil:
        for p in procs:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.terminate()
        _, alive = psutil.wait_procs(procs, timeout=KILL_WAIT_S)
        for p in alive:
            with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                p.kill()
    elif platform == "win32":
        for pid in pids:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
    else:
        def group_alive(pid):
            try:
                os.killpg(pid, 0)
            except (ProcessLookupError, PermissionError, OSError):
                return False
            return True

        deadline = time.monotonic() + KILL_WAIT_S
        left = list(pids)
        while left and time.monotonic() < deadline:
            left = [pid for pid in left if group_alive(pid)]
            time.sleep(0.05)
        for pid in left:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(pid, signal.SIGKILL)


def kill_all():
    """Kill every running program tree, all at once; from now on no job starts a program (call() → 130)."""
    _interrupted.set()
    with _live_lock:
        running = list(_live)
    kill_trees([p.pid for p in running if p.poll() is None])


def interrupt_signals(platform=None):
    sigs = [signal.SIGINT, signal.SIGTERM]
    if (platform or paths.platform_of()) == "win32":
        sigs.append(getattr(signal, "SIGBREAK", 21))  # Ctrl-Break
    elif hasattr(signal, "SIGHUP"):  # the terminal closed: the engines, in sessions of their own, are not told
        sigs.append(signal.SIGHUP)
    return sigs


def install_interrupt_handlers():
    """Ctrl-C / SIGTERM / SIGHUP / Ctrl-Break raise Interrupted in the main thread, once: a signal that comes while
    vidx is already shutting down (kill_all waits up to KILL_WAIT_S) is ignored. A signal ignored when vidx started
    (e.g. a background job of a non-interactive shell) stays ignored, as bash's trap did."""
    def handler(signum, frame):
        if _interrupted.is_set():  # shutting down already: a second Ctrl-C must not break off the kill
            return
        _interrupted.set()
        raise Interrupted

    for s in interrupt_signals():
        if signal.getsignal(s) != signal.SIG_IGN:
            signal.signal(s, handler)


class Context:
    """What a job body gets: run programs, write lines to the job's output (its log with -j > 1)."""

    def __init__(self, stream=None):
        self.stream = stream  # None: the terminal (vidx's own stdout/stderr)

    def say(self, msg):
        s = self.stream or sys.stderr
        s.write(msg + "\n")
        s.flush()

    def call(self, argv, stdout=None, drop=None, env=None):
        """Run a program, wait, return its exit status. stdout: a file path to write its output to (else the
        job's output); drop: stderr lines containing this text are not shown (Hugging Face "Fetching"). Its stderr
        goes through vidx when filtered, or re-encoded for a non-UTF-8 terminal (reencodes)."""
        if _interrupted.is_set():
            return 130
        out_file = open(stdout, "wb") if stdout else None  # noqa: SIM115 — closed below
        try:
            target = self.stream
            through = drop is not None or (target is None and reencodes(sys.stderr))
            p = spawn(argv, stdout=out_file or target, stderr=subprocess.PIPE if through else target, env=env)
            pump = None
            if through:  # copied in a thread: the main thread must stay in wait()'s slices
                pump = threading.Thread(target=relay, args=(p.stderr, target or sys.stderr, drop), daemon=True)
                pump.start()
            rc = wait(p)
            while pump and pump.is_alive():  # the rest of the program's stderr
                pump.join(POLL_S)
            _forget(p)
            return rc
        finally:
            if out_file:
                out_file.close()


def log_name(label):
    """Job label → its log file name, as the bash version made it (tr -c 'A-Za-z0-9@.-' '_' | tr -s _)."""
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9@.-]", "_", label)) + ".log"


def tail(path, n=5):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()[-n:]


def run_job(job, out, background):
    t0 = time.monotonic()
    if _interrupted.is_set():
        return
    if job.output and not out.fresh(job.output, job.params):
        log(f"{job.label}: cached")
        return
    lf = None
    if background:
        lf = out.join("logs", log_name(job.label))
        log(f"{job.label}: started (log: logs/{os.path.basename(lf)})")
        with open(lf, "w", encoding="utf-8", newline="\n") as stream:
            ok = job.run(Context(stream))
    else:
        log(job.label)
        ok = job.run(Context())
    if _interrupted.is_set():
        return
    if ok:
        if job.output:
            out.stamp(job.output, job.params)
        log(f"{job.label}: done in {int(time.monotonic() - t0)} s")
    else:
        out.add_failed(job.label)
        warn(f"{job.label} failed, continuing")
        if lf:
            sys.stderr.write("".join(f"    {line}\n" for line in tail(lf)))
            sys.stderr.flush()


def run_queue(queue, n, out):
    """Run the jobs in queue order, n at a time (n > 1: output to logs/<job>.log)."""
    if n <= 1:
        for job in queue:
            run_job(job, out, background=False)
        return
    os.makedirs(out.join("logs"), exist_ok=True)
    log(f"running {len(queue)} jobs, {n} at a time")
    pool = ThreadPoolExecutor(max_workers=n)
    try:
        pending = {pool.submit(run_job, job, out, True) for job in queue}
        while pending:  # a timeout, so that Ctrl-C reaches the main thread on Windows too
            done, pending = wait_futures(pending, timeout=POLL_S, return_when=FIRST_COMPLETED)
            for f in done:
                f.result()
    except BaseException:  # Ctrl-C, or an error in a job (OSError: disk full): stop the other jobs' programs too,
        kill_all()  # else their threads would keep vidx waiting until each engine ends on its own
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
