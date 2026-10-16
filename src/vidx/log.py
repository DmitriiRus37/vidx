"""Messages on stderr: ==> progress, warn:, error: (coloured on a terminal)."""
import os
import sys
import threading

_lock = threading.RLock()  # re-entrant: a Ctrl-C can arrive while a message is printed
_colours = {"log": "", "warn": "", "err": "", "end": ""}


class Fatal(Exception):
    """A usage or setup error: vidx prints "error: <message>" and exits 1."""


def enable_windows_vt(stream):
    """Turn on ANSI escape handling of a Windows console (off by default in conhost). False if not possible."""
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.windll.kernel32
        handle = msvcrt.get_osfhandle(stream.fileno())
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except (ImportError, AttributeError, OSError, ValueError):
        return False


def use_colours(stream=None, env=None, platform=None, enable_vt=enable_windows_vt):
    """Colours only on a terminal, not with NO_COLOR, and on Windows only if the console takes escapes."""
    stream = stream or sys.stderr
    env = os.environ if env is None else env
    platform = platform or sys.platform
    on = stream.isatty() and not env.get("NO_COLOR") and (platform != "win32" or enable_vt(stream))
    _colours.update(log="\033[1;36m", warn="\033[1;33m", err="\033[1;31m", end="\033[0m") if on else \
        _colours.update(log="", warn="", err="", end="")
    return on


def setup_streams(streams=None):
    """vidx's stdout and stderr on every OS. A file or pipe gets UTF-8 with LF, as vidx's files do: Windows
    would write its ANSI code page and CRLF there, a Latin-1 locale on macOS/Linux Latin-1, neither of which
    has the → of our messages or a Cyrillic file name. A terminal keeps its own encoding (UTF-8 bytes would
    garble every é of a Latin-1 terminal; the Windows console takes Unicode) and shows a character it has no
    code for as "?" instead of stopping vidx."""
    for s in streams or (sys.stdout, sys.stderr):
        if s.isatty():
            s.reconfigure(errors="replace")
        else:
            s.reconfigure(encoding="utf-8", errors="backslashreplace", newline="\n")


def _say(text):
    with _lock:
        print(text, file=sys.stderr, flush=True)


def log(msg):
    _say(f"{_colours['log']}==>{_colours['end']} {msg}")


def warn(msg):
    _say(f"{_colours['warn']}warn:{_colours['end']} {msg}")


def error(msg):
    _say(f"{_colours['err']}error:{_colours['end']} {msg}")
