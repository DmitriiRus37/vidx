"""The job queue: engines and frames+OCR, one by one or -j N at a time; process trees and Ctrl-C.

Every external program runs through spawn(): its own process group (POSIX: a new session; Windows:
CREATE_NEW_PROCESS_GROUP), stdin from the null device, PYTHONUTF8=1 for Python children. The terminal's
Ctrl-C then reaches only vidx, which kills every running tree (kill_tree: psutil's view of the
descendants, plus killpg on POSIX, or taskkill on Windows when psutil is missing) and exits 130.
"""
import contextlib
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

from vidx import paths
from vidx.log import log, warn

try:
    import psutil
except ImportError:  # optional on POSIX (killpg covers the tree); a dependency of the package
    psutil = None

KILL_WAIT_S = 3
_live = set()  # running Popen objects
_live_lock = threading.Lock()
_interrupted = threading.Event()


class Interrupted(BaseException):
    """Ctrl-C, SIGTERM or Ctrl-Break: kill everything, exit 130."""


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


def spawn(argv, platform=None, stdout=None, stderr=None, env=None):
    """Start a program in its own process group, registered for kill_all()."""
    kw = dict(stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, env=child_env(env))
    if (platform or paths.platform_of()) == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen([str(a) for a in argv], **kw)
    with _live_lock:
        _live.add(p)
    return p


def _forget(p):
    with _live_lock:
        _live.discard(p)


def kill_tree(pid, platform=None):
    """Terminate a process and all its descendants; kill what is still alive after KILL_WAIT_S."""
    platform = platform or paths.platform_of()
    procs = []
    if psutil:
        try:
            parent = psutil.Process(pid)
            procs = [*parent.children(recursive=True), parent]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            procs = []
    if platform != "win32":
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
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    else:
        deadline = time.monotonic() + KILL_WAIT_S
        while time.monotonic() < deadline:
            try:
                os.killpg(pid, 0)
            except (ProcessLookupError, PermissionError, OSError):
                return
            time.sleep(0.05)
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pid, signal.SIGKILL)


def kill_all():
    _interrupted.set()
    with _live_lock:
        running = list(_live)
    for p in running:
        if p.poll() is None:
            kill_tree(p.pid)


def interrupt_signals(platform=None):
    sigs = [signal.SIGINT, signal.SIGTERM]
    if (platform or paths.platform_of()) == "win32":
        sigs.append(getattr(signal, "SIGBREAK", 21))  # Ctrl-Break
    return sigs


def install_interrupt_handlers():
    """Ctrl-C / SIGTERM / Ctrl-Break raise Interrupted in the main thread. A signal ignored when vidx started
    (e.g. a background job of a non-interactive shell) stays ignored, as bash's trap did."""
    def handler(signum, frame):
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
        job's output); drop: stderr lines containing this text are not shown (Hugging Face "Fetching")."""
        if _interrupted.is_set():
            return 130
        out_file = open(stdout, "wb") if stdout else None  # noqa: SIM115 — closed below
        try:
            target = self.stream
            err = subprocess.PIPE if drop else (target or None)
            p = spawn(argv, stdout=out_file or target, stderr=err, env=env)
            if drop:
                dest = target or sys.stderr
                for raw in p.stderr:
                    line = raw.decode("utf-8", "replace")
                    if drop not in line:
                        dest.write(line)
                        dest.flush()
            rc = p.wait()
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
            done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for f in done:
                f.result()
    except BaseException:  # Ctrl-C: do not wait for the workers, whose programs are about to be killed
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
