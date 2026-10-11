"""vidx command line: help, find, setup and a run (the default command). Exit status: 0 all jobs succeeded,
3 some jobs failed (the rest is merged anyway), 1 usage or setup error, 130 interrupted."""
import os
import sys

from vidx import install, jobs, log, paths, texts
from vidx.options import APPLE_TIMEOUT_DEFAULT, ShowHelp, Usage, parse_run


def help_values():
    root = paths.checkout()
    return dict(VIDX_DIR=str(root) if root else "https://github.com/DmitriiRus37/vidx/blob/main",
                APPLE_TIMEOUT_DEFAULT=APPLE_TIMEOUT_DEFAULT, TOOLS=" ".join(install.TOOLS),
                HELPER_PKGS=" ".join(install.HELPER_PKGS),
                PY_VERSION=os.environ.get("VIDX_PYTHON") or install.PY_VERSION_DEFAULT,
                PY_GIGA_VERSION=os.environ.get("VIDX_PYTHON_GIGA") or install.PY_GIGA_VERSION_DEFAULT)


def show(text):
    sys.stdout.write(texts.render(text, **help_values()))
    sys.stdout.flush()


def find(args):
    """vidx find <out-dir> <regex> [source]: lib/find.py (standard library only) in the helper Python."""
    from vidx.pipeline import Runtime

    rt = Runtime()
    python = rt.helper_python if os.path.isfile(rt.helper_python) else sys.executable
    return jobs.Context().call([python, str(paths.LIB / "find.py"), *args])


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
    if cmd == "help":
        show({"run": texts.RUN, "find": texts.FIND, "setup": texts.SETUP}.get(args[1] if len(args) > 1 else "",
                                                                             texts.USAGE))
        return 0
    if cmd == "setup":
        if args[1:2] in (["-h"], ["--help"]):
            show(texts.SETUP)
            return 0
        from vidx import setup

        return setup.run()
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
        jobs.kill_all()
        log.warn("interrupted")
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(130)  # worker threads may still be waiting on killed programs
