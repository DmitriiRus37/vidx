"""A run from a URL (vidx.pipeline.Run.download) with yt-dlp faked: the output folder from the video id, the
files yt-dlp writes renamed, subtitles for the run's languages, failures."""
import json
import os

import pytest

from vidx import jobs, pipeline
from vidx.log import Fatal
from vidx.options import parse_run

URL = "https://example.com/watch?v=x"


class FakeYtdlp:
    """jobs.Context().call for yt-dlp: the download writes source.mp4 + info + description where -o says."""

    def __init__(self, rc=0, subs_rc=0, info=None, write_source=True):
        self.rc, self.subs_rc, self.info, self.write_source = rc, subs_rc, info, write_source
        self.calls = []

    def __call__(self):
        return self

    def call(self, argv, **kw):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        if "--write-subs" in argv or "--write-auto-subs" in argv:
            return self.subs_rc
        out = argv[argv.index("-o") + 1].rsplit(os.sep, 1)[0].replace("%%", "%")
        if self.write_source:
            open(os.path.join(out, "source.mp4"), "wb").close()
        with open(os.path.join(out, "info.info.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(self.info or {}, f)
        with open(os.path.join(out, "description.description"), "w", encoding="utf-8", newline="\n") as f:
            f.write("about soup")
        return self.rc

    def subs(self):
        return [c for c in self.calls if "--sub-langs" in c]


def make_run(monkeypatch, tmp_path, fake, *args, video_id="abc/123?"):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline.Runtime, "which", lambda self, tool: tool)
    monkeypatch.setattr(jobs, "capture", lambda argv, **k: (0, video_id + "\n"))
    monkeypatch.setattr(jobs, "Context", fake)
    return pipeline.Run(parse_run([URL, *args]), pipeline.Runtime(env={}))


def test_download_names_the_folder_after_the_sanitised_video_id(tmp_path, monkeypatch):
    fake = FakeYtdlp(info={"language": "de"})
    run = make_run(monkeypatch, tmp_path, fake, "-l", "fr")
    run.download()
    out = tmp_path / "abc_123__vidx-out"
    assert run.out.path == str(out)
    assert run.src == str(out / "source.mp4")
    assert (out / "info.json").exists() and (out / "description.txt").read_text(encoding="utf-8") == "about soup"
    assert not (out / "info.info.json").exists()
    assert (out / ".vidx" / "input").read_text(encoding="utf-8") == URL
    first = fake.calls[0]
    assert first[:4] == ["yt-dlp", "--ignore-config", "--no-warnings", "--no-playlist"] and first[-1] == URL
    # subtitles: manual then auto, original-language tracks of ru, en, the --lang list and the video's language
    manual, auto = fake.subs()
    assert "--write-subs" in manual and "--write-auto-subs" in auto
    langs = manual[manual.index("--sub-langs") + 1]
    assert langs == "ru,ru-orig,en,en-orig,fr,fr-orig,de,de-orig"
    assert (out / "subs").is_dir()


def test_a_second_run_reuses_the_download_and_the_subtitles(tmp_path, monkeypatch):
    fake = FakeYtdlp()
    make_run(monkeypatch, tmp_path, fake).download()
    (tmp_path / "abc_123__vidx-out" / "subs" / "manual.ru.json3").write_text("{}", encoding="utf-8", newline="\n")
    again = FakeYtdlp()
    run = make_run(monkeypatch, tmp_path, again)
    run.download()
    assert again.calls == [] and run.src.endswith("source.mp4")


def test_explicit_output_and_sub_langs_and_no_subs(tmp_path, monkeypatch):
    fake = FakeYtdlp(info={"language": "not a language!"})
    run = make_run(monkeypatch, tmp_path, fake, "-o", "my%out", "--sub-langs", "es")
    run.download()
    assert run.out.path == str(tmp_path / "my%out")
    assert f"{tmp_path / 'my%%out'}{os.sep}source.%(ext)s" in fake.calls[0]  # % escaped for yt-dlp's templates
    manual, _ = fake.subs()
    assert manual[manual.index("--sub-langs") + 1] == "es"

    fake = FakeYtdlp()
    run = make_run(monkeypatch, tmp_path, fake, "-o", "nosubs", "--no-subs")
    run.download()
    assert fake.subs() == []


def test_failed_subtitles_only_warn(tmp_path, monkeypatch, capsys):
    run = make_run(monkeypatch, tmp_path, FakeYtdlp(subs_rc=1))
    run.download()
    err = capsys.readouterr().err
    assert "manual subtitles failed" in err and "auto subtitles failed" in err


@pytest.mark.parametrize("fake", [FakeYtdlp(rc=1), FakeYtdlp(write_source=False)])
def test_download_failure(tmp_path, monkeypatch, fake):
    run = make_run(monkeypatch, tmp_path, fake)
    with pytest.raises(Fatal, match="download failed"):
        run.download()


def test_an_empty_video_id(tmp_path, monkeypatch):
    run = make_run(monkeypatch, tmp_path, FakeYtdlp(), video_id="")
    with pytest.raises(Fatal, match="could not read the video id"):
        run.download()


def test_source_routes_urls_to_the_download(tmp_path, monkeypatch):
    fake = FakeYtdlp()
    run = make_run(monkeypatch, tmp_path, fake)
    run.source()
    assert fake.calls and run.src.endswith("source.mp4")


def test_source_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = pipeline.Run(parse_run([str(tmp_path / "nope.mp4")]), pipeline.Runtime(env={}))
    with pytest.raises(Fatal, match="no such file"):
        run.source()
