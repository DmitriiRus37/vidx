"""OCR on Linux and Windows: RapidOCR (PaddleOCR models on onnxruntime) behind the Swift ocr helper's command
line and output, so vidx and merge.py treat it like Apple Vision.

usage: ocr_rapid.py <groups> <image>...   → one JSON line per image: {"file": ..., "text": ...}
<groups>: Vision recognition-language lists separated by ";" (languages.py --ocr), e.g. "ru-RU,en-US;zh-Hans,en-US".
Like Vision, a RapidOCR recogniser reads one script (the ch model turns Cyrillic into Latin look-alikes), so
each group maps to its models (models_of: eslav = Russian/Ukrainian/Belarusian + English, latin, en, ch =
Chinese + Japanese, arabic, devanagari, korean) and every model reads the image once. The first pass's lines
come first; a later pass adds lines whose boxes overlap an earlier line's by at most half; with several
passes the lines are sorted top to bottom. Unlike Vision, a recogniser reads other scripts as noise (the Cyrillic
model reads an Arabic line as "El lb"), so where two passes read the same line, the text that is more in its own
model's script wins (own_script); a tie keeps the earlier pass.
Detector: RapidOCR's default; recognisers: PP-OCRv5 mobile.
Models are downloaded on first use into VIDX_RAPIDOCR_MODELS (vidx sets <home>/models/rapidocr).
Known gap: Devanagari titles are not detected (hi).
"""
import json
import os
import sys
import unicodedata

MODEL_OF = {"ru": "eslav", "uk": "eslav", "be": "eslav", "en": "en", "zh": "ch", "ja": "ch", "ar": "arabic",
            "hi": "devanagari", "ko": "korean"}
OVERLAP = 0.5
# the letters each recogniser is for (Unicode character name prefixes); en/latin: Latin letters
SCRIPT = {"eslav": ("CYRILLIC",), "latin": ("LATIN",), "en": ("LATIN",), "arabic": ("ARABIC",),
          "devanagari": ("DEVANAGARI",), "korean": ("HANGUL",),
          "ch": ("CJK UNIFIED", "CJK COMPATIBILITY", "HIRAGANA", "KATAKANA")}


def models_of(group):
    """Vision locales of one group → RapidOCR recognition models, in order; "en" only alone: every other
    PP-OCRv5 recogniser reads Latin letters too (as each Vision group ends with en-US)."""
    models = list(dict.fromkeys(MODEL_OF.get(code.split("-")[0], "latin") for code in group))
    return [m for m in models if m != "en"] or models


def overlap(a, b):
    """Share of the smaller box (x0, y0, x1, y1) covered by the other."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return w * h / small if small > 0 else 0.0


def own_script(text, model):
    """Share of the letters of text that are in the script the model is for."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    names = SCRIPT[model]
    return sum(unicodedata.name(c, "").startswith(names) for c in letters) / len(letters)


class Reader:
    def __init__(self):
        self.engines = {}

    def engine(self, lang):
        if lang not in self.engines:
            from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

            params = {"Rec.lang_type": getattr(LangRec, lang.upper()), "Rec.ocr_version": OCRVersion.PPOCRV5,
                      "Rec.model_type": ModelType.MOBILE, "Global.log_level": "error"}
            root = os.environ.get("VIDX_RAPIDOCR_MODELS")
            if root:
                os.makedirs(root, exist_ok=True)
                params["Global.model_root_dir"] = root
            self.engines[lang] = RapidOCR(params=params)
        return self.engines[lang]

    def lines(self, path, lang):
        """[(box (x0, y0, x1, y1), text)] read by one model."""
        r = self.engine(lang)(path)
        if r.boxes is None or r.txts is None:
            return []
        res = []
        for pts, text in zip(r.boxes, r.txts, strict=False):
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            if text.strip():
                res.append(((min(xs), min(ys), max(xs), max(ys)), text.strip()))
        return res


def read_image(reader, path, groups):
    passes = [m for g in groups for m in models_of(g)]
    passes = list(dict.fromkeys(passes))
    lines = []  # [(box, text, share of the text in its model's script)]
    for lang in passes:
        try:
            got = reader.lines(path, lang)
        except Exception as e:  # noqa: BLE001 — one failed pass must not lose the others (like ocr.swift)
            print(f"ocr: {path} [{lang}]: {e}", file=sys.stderr)
            continue
        for b, t in got:
            share = own_script(t, lang)
            same = [i for i, (o, _, _) in enumerate(lines) if overlap(b, o) > OVERLAP]
            if not same:
                lines.append((b, t, share))
            elif all(share > lines[i][2] for i in same):  # the earlier pass read this line as noise
                first = same[0]
                lines[first] = (b, t, share)
                lines = [x for i, x in enumerate(lines) if i not in same[1:]]
    if len(passes) > 1:  # each pass returns its lines top to bottom; interleave them
        height = max((b[3] for b, _, _ in lines), default=1) or 1
        lines = [x for _, x in sorted(enumerate(lines), key=lambda e: (round((e[1][0][1] + e[1][0][3]) / 2 / height
                                                                               / 0.01), e[0]))]
    return "\n".join(t for _, t, _ in lines)


def main():
    if len(sys.argv) < 3:
        print("usage: ocr_rapid.py <lang,lang;lang,...> <image>...", file=sys.stderr)
        sys.exit(2)
    sys.stdout.reconfigure(newline="\n")
    groups = [[x for x in g.split(",") if x] for g in sys.argv[1].split(";")]
    groups = [g for g in groups if g]
    reader = Reader()
    for path in sys.argv[2:]:
        text = ""
        try:
            from PIL import Image

            with Image.open(path) as im:
                im.verify()
            text = read_image(reader, path, groups)
        except OSError:
            print(f"ocr: {path}: not an image", file=sys.stderr)
        print(json.dumps({"file": os.path.basename(path), "text": text}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
