"""vidx run options: the bash option loop's semantics and messages, in Python (vidx.options)."""
import pytest

from vidx.options import Usage, parse_run, secs_text

AV = "clip.mp4"


def parse(*args, env=None):
    return parse_run(list(args), env or {})


@pytest.mark.parametrize("args, msg", [
    ([AV, "-g", "nope.txt"], "no such glossary: nope.txt"),
    ([AV, "--cut", "36:30"], "--cut needs START-END, e.g. 36:30-50:00"),
    ([AV, "--cut", "50:00-36:30"], "--cut 50:00-36:30: start must be before end"),
    ([AV, "--cut", "10-10"], "--cut 10-10: start must be before end"),
    ([AV, "-e", "whisper,foo"], "unknown engine 'foo' (known: whisper,parakeet,gigaam,turbo,apple,vosk)"),
    ([AV, "-e", "whisper,,turbo"], "unknown engine ''"),
    ([AV, "-l", "Russian"], "--lang must be auto or a list like ru or ru,en"),
    ([AV, "-l", "ru,"], "--lang must be auto or a list like ru or ru,en"),
    ([AV, "-j", "0"], "--jobs must be a positive number"),
    ([AV, "-j", "02"], "--jobs must be a positive number"),
    ([AV, "--scene", "high"], "--scene must be a number, got 'high'"),
    ([AV, "--roi", ".5"], "--roi must be a number, got '.5'"),
    ([AV, "-o"], "option -o needs a value"),
    ([AV, "-o", ""], "option -o needs a value"),
    ([AV, "--frobnicate"], "unknown option --frobnicate (see: vidx help run)"),
])
def test_refused(args, msg):
    with pytest.raises(Usage) as e:
        parse(*args)
    assert str(e.value).startswith(msg)


@pytest.mark.parametrize("value", ["inf", "nan", "1e12", "0", "-5", "abc", "86401", "99999999999999999999", "012"])
def test_apple_timeout_refused(value):
    with pytest.raises(Usage, match=f"VIDX_APPLE_TIMEOUT must be 1..86400 seconds, got '{value}'"):
        parse(AV, env={"VIDX_APPLE_TIMEOUT": value})


def test_apple_timeout_default_and_max():
    assert parse(AV).apple_timeout == "300"
    assert parse(AV, env={"VIDX_APPLE_TIMEOUT": ""}).apple_timeout == "300"  # ${VAR:-300}
    assert parse(AV, env={"VIDX_APPLE_TIMEOUT": "86400"}).apple_timeout == "86400"


def test_defaults():
    o = parse(AV)
    assert (o.input, o.out, o.lang, o.glossary) == (AV, "", "auto", "")
    assert o.engines == "whisper,parakeet,gigaam,turbo,apple,vosk"
    assert (o.scene, o.every, o.dedup, o.roi, o.crop, o.maxh) == ("0.3", "10", "6", "1", "0", "1080")
    assert (o.force, o.jobs, o.sub_langs, o.cuts) == (False, 1, "", [])
    assert (o.do_asr, o.do_frames, o.do_ocr, o.do_subs) == (True, True, True, True)
    assert o.all_frames is False


def test_values_taken_as_given():
    o = parse(AV, "-o", "-l", "--meet", "--scene", "0.25", "-f", "--no-ocr", "--no-subs", "-j", "3")
    assert o.out == "-l"  # like bash: the next argument is the value, whatever it looks like
    assert (o.roi, o.crop, o.scene, o.jobs) == ("0.75", "1", "0.25", 3)
    assert (o.force, o.do_ocr, o.do_subs) == (True, False, False)


def test_all_frames():
    assert parse(AV, "--all-frames").all_frames is True


def test_later_option_wins():
    assert parse(AV, "--meet", "--roi", "0.5").roi == "0.5"
    assert parse(AV, "--roi", "0.5", "--meet").roi == "0.75"


def test_languages_without_repeats():
    assert parse(AV, "-l", "ru,en,ru").lang == "ru,en"
    assert parse(AV, "-l", "ru,ru").lang == "ru"


def test_glossary_absolute(tmp_path, monkeypatch):
    (tmp_path / "g.txt").write_text("prompt: x\n", encoding="utf-8", newline="\n")
    monkeypatch.chdir(tmp_path)
    assert parse(AV, "-g", "g.txt").glossary == str((tmp_path / "g.txt").resolve())


def test_cut_seconds_formatted_like_awk():
    assert parse(AV, "--cut", "36:30-50:00", "--cut", "1:02:03.5-1:02:04").cuts == ["2190-3000", "3723.5-3724"]
    assert secs_text("12345.678") == "12345.7"  # awk's %.6g
    assert secs_text("1::2") == "3602"  # an empty field counts 0
    assert secs_text("1.2.3") == "1.2"  # awk reads the number prefix


def test_help_inside_run_options():
    from vidx.options import ShowHelp

    with pytest.raises(ShowHelp):
        parse(AV, "-j", "2", "--help")
