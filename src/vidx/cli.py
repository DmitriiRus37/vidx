"""vidx command line: help, find, setup and a run (the default command). Exit status: 0 all jobs succeeded,
3 some jobs failed (the rest is merged anyway), 1 usage or setup error or an unexpected error, 130 interrupted."""
import os
import sys
import traceback

from vidx import jobs, log, paths, texts
from vidx.options import APPLE_TIMEOUT_DEFAULT, ShowHelp, Usage, parse_run


def help_platform(env=None):
    """This platform; in fake-engine runs (tests) the one they simulate."""
    env = os.environ if env is None else env
    fake = env.get("VIDX_FAKE_ENGINES") == "1" and env.get("VIDX_FAKE_PLATFORM")
    return fake or paths.platform_of()


def help_values(platform=None):
    platform = platform or help_platform()
    root = paths.checkout()
    darwin = platform == "darwin"
    table = texts.ENGINE_TABLE["darwin" if darwin else "portable"]
    return dict(VIDX_DIR=str(root) if root else "https://github.com/DmitriiRus37/vidx/blob/main",
                APPLE_TIMEOUT_DEFAULT=APPLE_TIMEOUT_DEFAULT, MACHINE="this Mac" if darwin else "this computer",
                MACHINE_SHORT="Mac" if darwin else "machine", OCR_NAME="Apple Vision" if darwin else "RapidOCR",
                ENGINE_TABLE=texts.render(table, APPLE_TIMEOUT_DEFAULT=APPLE_TIMEOUT_DEFAULT))


def show(text):
    sys.stdout.write(texts.render(text, **help_values()))
    sys.stdout.flush()


def find(args):
    """vidx find <out-dir> <regex> [source]: lib/find.py (standard library only) in the helper Python. Its
    hits (UTF-8) are printed by vidx, so a terminal or a redirect gets them as it gets vidx's messages."""
    from vidx.pipeline import Runtime

    rt = Runtime()
    python = rt.helper_python if os.path.isfile(rt.helper_python) else sys.executable
    out, regex, *source = args  # the regex is text: UTF-8 in every locale (jobs.Text); the rest are file names
    rc, hits = jobs.capture([python, str(paths.LIB / "find.py"), out, jobs.Text(regex), *source])
    sys.stdout.write(hits)
    sys.stdout.flush()
    return rc


def run(args):
    from vidx.pipeline import Run, Runtime

    try:
        opts = parse_run(args)
    except ShowHelp:
        show(texts.RUN)
        return 0
    return Run(opts, Runtime()).main()


def dispatch(args):
    if not args:
        show(texts.USAGE)
        return 1
    cmd = args[0]
    if cmd in ("-h", "--help"):
        show(texts.USAGE)
        return 0
    from vidx import setup

    if cmd == "help":
        show({"run": texts.RUN, "find": texts.FIND, "setup": setup.__doc__}.get(args[1] if len(args) > 1 else "",
                                                                               texts.USAGE))
        return 0
    if cmd == "setup":
        if args[1:2] in (["-h"], ["--help"]):
            show(setup.__doc__)
            return 0
        return setup.run(args[1:])
    if cmd == "find":
        if args[1:2] in (["-h"], ["--help"]):
            show(texts.FIND)
            return 0
        if len(args) < 3:
            show(texts.FIND)
            return 1
        return find(args[1:])
    return run(args)


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    log.setup_streams()
    log.use_colours()
    jobs.install_interrupt_handlers()
    try:
        return dispatch(args)
    except (Usage, log.Fatal) as e:
        log.error(str(e))
        return 1
    except jobs.Interrupted:
        log.warn("interrupted")  # first: the kill may take KILL_WAIT_S
        return stop(130)
    except Exception:  # noqa: BLE001 — a bug or the OS (disk full): no engine may outlive vidx
        traceback.print_exc()
        log.error("unexpected error (above); stopping every program vidx started")
        return stop(1)


def stop(code):
    """Kill every running program tree and exit now: worker threads may still be waiting on killed programs."""
    jobs.kill_all()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
