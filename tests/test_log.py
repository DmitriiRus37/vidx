"""Messages and their colours (vidx.log), including the Windows console rules."""
import io

from vidx import log


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_colours_only_on_a_terminal_without_no_color():
    assert log.use_colours(Tty(), {}, "linux")
    assert not log.use_colours(Tty(), {"NO_COLOR": "1"}, "linux")
    assert not log.use_colours(io.StringIO(), {}, "darwin")
    log.use_colours(io.StringIO(), {}, "linux")  # leave them off for the other tests


def test_windows_colours_need_the_console_to_take_escapes():
    assert log.use_colours(Tty(), {}, "win32", enable_vt=lambda s: True)
    assert not log.use_colours(Tty(), {}, "win32", enable_vt=lambda s: False)
    assert not log.enable_windows_vt(io.StringIO())  # no console here: refused, not an exception


def test_message_prefixes(capsys):
    log.use_colours(io.StringIO(), {}, "linux")
    log.log("merging")
    log.warn("x failed, continuing")
    log.error("bad")
    assert capsys.readouterr().err == "==> merging\nwarn: x failed, continuing\nerror: bad\n"


class Stream(io.TextIOWrapper):
    def __init__(self, encoding, newline=None, tty=False):
        super().__init__(io.BytesIO(), encoding=encoding, newline=newline)
        self.tty = tty

    def isatty(self):
        return self.tty

    def printed(self, text):
        self.write(text)
        self.flush()
        return self.buffer.getvalue()


def test_redirected_streams_become_utf8_with_lf_on_every_os():
    """A file or pipe gets what vidx's files get: Windows' ANSI code page and CRLF, or a Latin-1 locale on
    macOS/Linux, cannot write the → of our messages or a Cyrillic file name."""
    for encoding, newline in (("cp1252", "\r\n"), ("latin-1", None), ("utf-8", None)):
        s = Stream(encoding, newline)
        log.setup_streams([s])
        assert s.printed("done → Видео\n") == "done → Видео\n".encode()


def test_a_terminal_keeps_its_encoding_and_shows_what_it_cannot_as_a_question_mark():
    """A terminal shows text in its own encoding: UTF-8 bytes would turn every é of a Latin-1 terminal into
    mojibake, so it keeps it, and a character it has no code for is replaced instead of stopping vidx."""
    s = Stream("latin-1", "\n", tty=True)  # a terminal's line ends are its own: LF here, on Windows too
    log.setup_streams([s])
    assert s.encoding == "latin-1"
    assert s.printed("café — Видео\n") == b"caf\xe9 ? ?????\n"
    utf8 = Stream("utf-8", "\n", tty=True)
    log.setup_streams([utf8])
    assert utf8.printed("bad name \udcff\n") == b"bad name ?\n"  # an undecodable file name (surrogateescape)


def fake_console(monkeypatch, mode, set_ok=True):
    """ctypes.windll.kernel32 and msvcrt of a Windows console whose mode is `mode` (None: not a console)."""
    import ctypes
    import sys
    import types

    calls = []

    def get_mode(handle, ref):
        if mode is None:
            return 0
        ref._obj.value = mode
        return 1

    def set_mode(handle, value):
        calls.append((handle, value))
        return 1 if set_ok else 0

    kernel32 = types.SimpleNamespace(GetConsoleMode=get_mode, SetConsoleMode=set_mode)
    monkeypatch.setattr(ctypes, "windll", types.SimpleNamespace(kernel32=kernel32), raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", types.SimpleNamespace(get_osfhandle=lambda fd: 1000 + fd))
    return calls


class Fd(io.StringIO):
    def fileno(self):
        return 7


def test_windows_vt_is_switched_on_keeping_the_console_mode(monkeypatch):
    calls = fake_console(monkeypatch, mode=0x0003)
    assert log.enable_windows_vt(Fd())
    assert calls == [(1007, 0x0007)]  # ENABLE_VIRTUAL_TERMINAL_PROCESSING added to what was there


def test_windows_vt_refused(monkeypatch):
    assert not fake_console(monkeypatch, mode=None) and not log.enable_windows_vt(Fd())  # not a console
    fake_console(monkeypatch, mode=0x0003, set_ok=False)
    assert not log.enable_windows_vt(Fd())  # an old conhost without VT support
