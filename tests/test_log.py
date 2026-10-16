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


def test_windows_redirected_streams_become_utf8_with_lf():
    raw = io.BytesIO()
    s = io.TextIOWrapper(raw, encoding="cp1252", newline="\r\n")  # Windows' redirected stdout/stderr
    log.setup_streams("win32", [s])
    s.write("done → Видео\n")
    s.flush()
    assert raw.getvalue() == "done → Видео\n".encode()
    other = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    log.setup_streams("linux", [other])
    assert other.encoding == "cp1252"  # elsewhere untouched
