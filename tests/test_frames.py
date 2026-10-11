"""frames.py: near-duplicate detection on grey thumbnails."""
import io

import frames
from PIL import Image, ImageDraw

DEDUP = 6  # vidx default --dedup


def slide(lines):
    im = Image.new("RGB", (1920, 1080), "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((120, 100 + 40 * i), line, fill="black", font_size=28)
    return im


def recompressed(im, quality=70):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue()))


def test_same_slide_is_duplicate():
    a = slide(["Q3 review", "Revenue grew 12 %"])
    assert frames.duplicate(frames.signature(a, 1), frames.signature(recompressed(a), 1), DEDUP)


def test_small_text_change_on_white_slide_is_not_duplicate():
    """A new 3-line bullet block in 28 px text (~1 % of the pixels; 1-2 lines stay below that)."""
    a = slide(["Q3 review", "Revenue grew 12 %"])
    b = slide(["Q3 review", "Revenue grew 12 %", "Next: the Q4 plan for every region and every team",
               "Owners: sales, marketing, platform, support", "Deadline: end of November, review weekly"])
    sa, sb = frames.signature(a, 1), frames.signature(b, 1)
    assert abs(sa - sb).mean() < DEDUP  # the mean barely moves...
    assert not frames.duplicate(sa, sb, DEDUP)  # ...but enough pixels changed


def test_roi_ignores_the_right_side():
    a = slide(["Q3 review"])
    b = a.copy()
    ImageDraw.Draw(b).rectangle([1500, 0, 1920, 1080], fill="black")  # e.g. Meet participant tiles
    assert not frames.duplicate(frames.signature(a, 1), frames.signature(b, 1), DEDUP)
    assert frames.duplicate(frames.signature(a, 0.75), frames.signature(b, 0.75), DEDUP)


def label_height(font):
    from PIL import Image, ImageDraw

    box = ImageDraw.Draw(Image.new("RGB", (200, 50))).textbbox((0, 0), "01:02:03", font=font)
    return box[3] - box[1]


def test_contact_sheet_label_font_is_readable_without_arial(monkeypatch):
    """Linux and Windows have no /System/Library/Fonts: the label falls back to Pillow's own scalable font at
    the same size, not to its 10 px bitmap font."""
    from PIL import ImageFont

    real = ImageFont.truetype

    def no_arial(path, *a, **k):
        if "Arial" in str(path):
            raise OSError("cannot open resource")
        return real(path, *a, **k)

    monkeypatch.setattr(ImageFont, "truetype", no_arial)
    assert label_height(frames.label_font()) >= 15  # digits of a 22 px font
