"""Drop near-duplicate frames, optionally crop to the screen-share area, build contact sheets.

usage: frames.py <raw_dir> <showinfo.log> <out_dir> <threshold> <roi> <crop 0|1>
  threshold  mean abs pixel diff on a 240x135 grey thumbnail below which a frame is a duplicate,
             unless more than CHANGED_SHARE of the pixels changed by > CHANGED_LEVEL (small text on
             a white slide barely moves the mean)
  roi        left fraction of the width to compare and crop (0.75 ignores Google Meet participant tiles)
  crop       1 = cut the frame to the bounding box of non-dark pixels inside roi
raw_dir holds ffmpeg's 000001.jpg... in the order showinfo logged them.
Writes out_dir/unique/HH-MM-SS.jpg, out_dir/sheets/sheet-NN.jpg, and out_dir/frames.json last (so an
interrupted run never looks finished).
"""
import os
import re
import sys

import numpy as np
from common import dump_json, hms
from PIL import Image, ImageDraw, ImageFont

THUMB = (240, 135)
LOOKBACK = 6  # also compare with a few earlier keepers: slides get revisited
CHANGED_LEVEL, CHANGED_SHARE = 25, 0.01
DARK_LEVEL, CONTENT_SHARE = 45, 0.02  # crop: a row/column is content if > 2 % of it is brighter than 45
SHEET_COLS, SHEET_FRAMES, TILE_W, TILE_H = 4, 20, 480, 270

raw_dir, log, out_dir = sys.argv[1:4]
thr, roi, crop = float(sys.argv[4]), float(sys.argv[5]), sys.argv[6] == "1"

with open(log, errors="replace") as f:
    times = [float(m.group(1)) for line in f if " n:" in line and (m := re.search(r"pts_time:\s*([\d.]+)", line))]
raw = sorted(f for f in os.listdir(raw_dir) if f.endswith(".jpg"))
if len(times) != len(raw):
    print(f"  warn: {len(raw)} frames vs {len(times)} timestamps", file=sys.stderr)


def signature(im):
    a = np.asarray(im.convert("L").resize(THUMB), dtype=np.float32)
    return a[:, :max(1, int(THUMB[0] * roi))]


def duplicate(s, p):
    d = np.abs(s - p)
    return d.mean() < thr and (d > CHANGED_LEVEL).mean() < CHANGED_SHARE


def cropped(im):
    a = np.asarray(im.convert("RGB")).astype(int)[:, :int(im.width * roi)]
    bright = a.mean(2) > DARK_LEVEL
    rows = np.where(bright.mean(1) > CONTENT_SHARE)[0]
    cols = np.where(bright.mean(0) > CONTENT_SHARE)[0]
    if len(rows) < 10 or len(cols) < 10:
        return im
    return im.crop((int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1))


uniq = os.path.join(out_dir, "unique")
os.makedirs(uniq, exist_ok=True)
kept, sigs, used = [], [], set()
for name, t in zip(raw, times, strict=False):  # mismatch already warned about above
    with Image.open(os.path.join(raw_dir, name)) as im:
        s = signature(im)
        if any(duplicate(s, p) for p in sigs[-LOOKBACK:]):
            continue
        out, k = hms(t, "-") + ".jpg", 1
        while out in used:  # several kept frames within one second
            out, k = f"{hms(t, '-')}_{k}.jpg", k + 1
        used.add(out)
        (cropped(im) if crop else im).convert("RGB").save(os.path.join(uniq, out), quality=90)
    kept.append({"file": out, "t": round(t, 2)})
    sigs.append(s)

sheets = os.path.join(out_dir, "sheets")
os.makedirs(sheets, exist_ok=True)
try:
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 22)
except OSError:
    font = ImageFont.load_default()
for p in range(0, len(kept), SHEET_FRAMES):
    chunk = kept[p:p + SHEET_FRAMES]
    sheet = Image.new("RGB", (SHEET_COLS * TILE_W, ((len(chunk) + SHEET_COLS - 1) // SHEET_COLS) * TILE_H), "black")
    draw = ImageDraw.Draw(sheet)
    for i, k in enumerate(chunk):
        with Image.open(os.path.join(uniq, k["file"])) as im:
            tile = im.convert("RGB")
        tile.thumbnail((TILE_W, TILE_H))
        x, y = (i % SHEET_COLS) * TILE_W, (i // SHEET_COLS) * TILE_H
        sheet.paste(tile, (x, y))
        draw.rectangle([x, y, x + 112, y + 28], fill="red")
        draw.text((x + 4, y + 2), hms(k["t"]), font=font, fill="white")
    sheet.save(os.path.join(sheets, f"sheet-{p // SHEET_FRAMES + 1:02d}.jpg"), quality=80)

dump_json(kept, os.path.join(out_dir, "frames.json"))
print(f"  frames: {len(raw)} raw -> {len(kept)} unique", file=sys.stderr)
