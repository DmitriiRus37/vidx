"""lib/ocr_rapid.py (OCR on Linux/Windows) with a stub rapidocr module: the Swift helper's command line
(Vision language groups) in, the same JSON lines out; one RapidOCR model per script, merged by position."""
import json
import sys
import types

import numpy as np
import ocr_rapid
import pytest
from PIL import Image


@pytest.mark.parametrize("group, models", [
    (["ru-RU", "en-US"], ["eslav"]), (["en-US"], ["en"]), (["de-DE", "en-US"], ["latin"]),
    (["ru-RU", "de-DE", "en-US"], ["eslav", "latin"]), (["ru-RU", "uk-UA", "en-US"], ["eslav"]),
    (["zh-Hans", "en-US"], ["ch"]), (["ja-JP", "zh-Hans", "en-US"], ["ch"]), (["ar-SA", "en-US"], ["arabic"]),
    (["hi-IN", "en-US"], ["devanagari"]), (["ko-KR", "en-US"], ["korean"]), (["fr-FR", "it-IT", "en-US"], ["latin"]),
])
def test_vision_groups_to_rapidocr_models(group, models):
    assert ocr_rapid.models_of(group) == models


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# what each stub model reads on the ru+zh slide: (box, text); like the real models, ch misreads Cyrillic
READS = {
    "eslav": [(box(70, 170, 630, 240), "Погода на выходные"), (box(70, 450, 630, 510), "Деплой в Kubernetes")],
    "ch": [(box(72, 172, 632, 238), "NoroAa Ha BbixoAHble"), (box(74, 310, 530, 380), "周末的天气预报"),
           (box(71, 452, 628, 512), "Aennoǔ B Kubernetes")],
    # the ru+ar slide: the Cyrillic model turns the Arabic line into Latin noise, the Arabic one reads it
    "arabic": [(box(70, 160, 512, 250), "طقس نهاية الأسبوع"), (box(76, 315, 631, 381), "Torona Ha BbixogHble")],
}


class StubEngine:
    def __init__(self, params):
        self.lang = params["Rec.lang_type"].value
        StubEngine.made.append(params)

    def __call__(self, path):
        rows = READS.get(self.lang, [])
        return types.SimpleNamespace(boxes=np.array([b for b, _ in rows]) if rows else None,
                                     txts=tuple(t for _, t in rows) if rows else None)


@pytest.fixture
def stub(monkeypatch, tmp_path):
    StubEngine.made = []
    enum = lambda *names: types.SimpleNamespace(**{n.upper(): types.SimpleNamespace(value=n) for n in names})  # noqa: E731
    mod = types.SimpleNamespace(RapidOCR=StubEngine, LangRec=enum("eslav", "latin", "en", "ch", "arabic", "devanagari",
                                                                  "korean"),
                                OCRVersion=types.SimpleNamespace(PPOCRV5="PP-OCRv5"),
                                ModelType=types.SimpleNamespace(MOBILE="mobile"))
    monkeypatch.setitem(sys.modules, "rapidocr", mod)
    monkeypatch.setenv("VIDX_RAPIDOCR_MODELS", str(tmp_path / "models"))
    img = tmp_path / "00-01-02.jpg"
    Image.new("RGB", (1280, 720), "white").save(img)
    return img


def run(argv, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ocr_rapid.py", *map(str, argv)])
    ocr_rapid.main()
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def test_one_pass_per_script_merged_top_to_bottom(stub, monkeypatch, capsys):
    (row,) = run(["ru-RU,en-US;zh-Hans,en-US", stub], monkeypatch, capsys)
    assert row == {"file": "00-01-02.jpg", "text": "Погода на выходные\n周末的天气预报\nДеплой в Kubernetes"}
    used = [p["Rec.lang_type"].value for p in StubEngine.made]
    assert used == ["eslav", "ch"]
    p = StubEngine.made[0]
    assert (p["Rec.ocr_version"], p["Rec.model_type"]) == ("PP-OCRv5", "mobile")
    assert p["Global.model_root_dir"].endswith("models")


def test_single_group_keeps_reading_order(stub, monkeypatch, capsys):
    (row,) = run(["zh-Hans,en-US", stub], monkeypatch, capsys)
    assert row["text"].splitlines() == ["NoroAa Ha BbixoAHble", "周末的天气预报", "Aennoǔ B Kubernetes"]


def test_a_line_goes_to_the_model_of_its_script(stub, monkeypatch, capsys):
    """Unlike Vision, a RapidOCR model reads other scripts as noise: where two passes read the same line, the
    one whose text is more in its own script wins."""
    READS["eslav"] = [(box(70, 165, 510, 251), "El lb"), (box(76, 315, 631, 381), "Погода на выходные")]
    try:
        (row,) = run(["ru-RU,en-US;ar-SA,en-US", stub], monkeypatch, capsys)
    finally:
        READS["eslav"] = [(box(70, 170, 630, 240), "Погода на выходные"),
                          (box(70, 450, 630, 510), "Деплой в Kubernetes")]
    assert row["text"].splitlines() == ["طقس نهاية الأسبوع", "Погода на выходные"]


@pytest.mark.parametrize("text, model, share", [("Погода", "eslav", 1.0), ("Деплой в Kubernetes", "eslav", 7 / 17),
                                                ("El lb", "eslav", 0.0), ("周末的天气", "ch", 1.0),
                                                ("週末の天気予報", "ch", 1.0), ("طقس", "arabic", 1.0),
                                                ("Größe", "latin", 1.0), ("", "latin", 0.0), ("123", "en", 0.0)])
def test_own_script_share(text, model, share):
    assert ocr_rapid.own_script(text, model) == pytest.approx(share)


def test_nothing_read(stub, monkeypatch, capsys):
    assert run(["hi-IN,en-US", stub], monkeypatch, capsys) == [{"file": "00-01-02.jpg", "text": ""}]


def test_unreadable_image_reported(stub, monkeypatch, capsys, tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_text("not an image", encoding="utf-8", newline="\n")
    monkeypatch.setattr(sys, "argv", ["ocr_rapid.py", "ru-RU,en-US", str(bad), str(stub)])
    ocr_rapid.main()
    out, err = capsys.readouterr()
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows[0] == {"file": "bad.jpg", "text": ""} and rows[1]["text"].startswith("Погода")
    assert "bad.jpg" in err


def test_usage(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ocr_rapid.py", "ru-RU"])
    with pytest.raises(SystemExit) as e:
        ocr_rapid.main()
    assert e.value.code == 2
