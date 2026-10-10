# vidx

Extract pictures and text from a video file or a YouTube (any yt-dlp) URL. It runs every local speech
recognizer available on Apple Silicon, OCRs the screen, and merges everything into one transcript.
Everything runs on the Mac; audio and video are never uploaded.

Built for long Russian-language meeting recordings with screen sharing; it works for any language
whisper knows, and handles videos that switch between Russian and English.

```bash
vidx setup                                         # once: tools + ~13 GB of models
vidx ~/Downloads/meeting.mp4 --meet -j 3           # → ./meeting_vidx-out/transcript.md
vidx "https://www.youtube.com/watch?v=VIDEO_ID" -g glossaries/example.txt
vidx find meeting_vidx-out 'kubernetes|helm'       # phrase → timecode
vidx help run                                      # every option
```

Requirements: macOS 26+ on Apple Silicon (MLX, SpeechTranscriber), Homebrew, Xcode command line tools
(`swiftc`). To call it from anywhere: `ln -s "$PWD/vidx" ~/.local/bin/vidx` (any directory on `PATH`).

## How it works

```
input ─► 1 source ─► 2 audio ─► 3 language ─┬─(mixed)─► parakeet ─► language regions ─┐
                                            └──────────────────────────────────────────┤
                                                                                       ▼
                             job queue (-j N):  4 speech engines  │  5 frames ─► OCR
                                                                                       ▼
                                                                                   6 merge
```

Every step writes its result into the output folder, by default `./<name>_vidx-out/` in the current
directory (`-o` to change). Along with each result vidx stores the options it was made with
(`.vidx/<file>.params`). A step is skipped when its result exists **and** was made with the same
options, so:

- an interrupted run resumes: steps that did not finish never wrote their stamp and run again;
- changing an option reruns only the steps it affects (`--scene` redoes frames and OCR, `-l` redoes
  the language-bound engines, `-g` redoes whisper);
- `-f` redoes everything, including the download.

`.vidx/` also marks the folder as made by vidx: `-o` refuses a non-empty folder without it, so vidx
never overwrites or deletes files it did not create. A different input file or URL in the same folder
starts over.

### 1. Source

- **Local file**: symlinked as `source.<ext>`, never copied.
- **URL**: yt-dlp downloads the video (≤ 1080p, `--max-height`), `info.json` (title, chapters,
  language), `description.txt` and `thumbnail.jpg`. Playlist URLs are reduced to the one video; yt-dlp
  config files are ignored (`--ignore-config`).
- **YouTube subtitles**: two more yt-dlp passes, one for manual and one for automatic captions, so
  the two kinds land in separate files (`subs/manual.ru.json3`, `subs/auto.en.json3`). Only
  original-language tracks are requested: ru, en, every `--lang` language and the video's own
  language, plus their `-orig` variants. Without the list YouTube offers dozens of machine
  translations. `--sub-langs` overrides it.
  For mixed-language videos this fetches both the ru and en tracks when they exist. YouTube's
  automatic captions, though, are generated in one language only (the video's main one), so the
  other language comes out garbled there; the merge uses each track only inside regions of its
  language, and the speech engines cover the rest.

### 2. Audio

ffmpeg → `audio.wav`, 16 kHz mono 16-bit PCM, the format every engine expects. It is written to a
temporary name first and renamed when complete. A file without an audio track stops here with a
message; `--no-asr` skips steps 2–4 and gives pictures and OCR only.

### 3. Language

`--lang` takes `auto`, one language (`ru`) or a list (`ru,en`).

`auto` ([lib/detect_lang.py](lib/detect_lang.py)): whisper turbo detects the language of evenly
spaced 10 s clips, one per minute of audio (5 to 30 clips). The most frequent language is always kept;
another one only if it wins at least 2 clips **and** 20 % of them, so a single misdetected clip does
not make a video mixed. No speech at all → `ru`. The result is cached in `lang.txt`. Short videos and
short switches (one English question in a Russian meeting) are therefore not detected as mixed: pass
`-l ru,en` explicitly.

Russian speech with English jargon (Kubernetes, deploy, Jenkins) is **not** mixed: use `ru` and a
glossary. Mixed means whole passages in different languages.

**Language regions (mixed runs only).** Engines that are told a language misbehave on the other
one: whisper `--language en` *translates* Russian speech into English, GigaAM turns English speech
into Latin-letter gibberish. So before the job queue:

1. parakeet runs (it is not told a language and handles both).
2. [lib/regions.py](lib/regions.py) labels each parakeet sentence by script: more Cyrillic than
   Latin letters → ru, otherwise the other language. Sentences without letters keep the previous
   label. This separates Cyrillic from Latin languages; it cannot tell English from German.
3. parakeet often drops a few seconds exactly where the speaker switches. Every gap longer than 1 s
   gets the language whisper turbo detects on that audio; shorter or silent gaps are split between
   their neighbours.
4. Consecutive pieces with the same label become a region → `regions.json`
   (`[{start, end, lang}]`).

On a test video (Russian 0–21.7 s, English 21.7–39.1 s, Russian after) the regions came out as
0–21.4, 21.4–38.9, 38.9–end. If parakeet or the split fails, the run continues with the first
language only.

### 4. Speech engines (job queue)

Each engine writes `asr/<name>.json`: a list of `{start, end, text}` segments. In a mixed run, the
engines that are told the language run once per language and get an `@lang` suffix
(`whisper-large-v3@en`); whisper and turbo then transcribe only that language's regions
(`--clip-timestamps`), GigaAM only the Russian regions. Vosk and Apple run on the whole file; the
merge ignores their output outside their language.

| Engine | Model | Language | Runs on | Notes |
|---|---|---|---|---|
| whisper | whisper-large-v3 (mlx-whisper) | forced, once per language | GPU | glossary prompt, `--condition-on-previous-text False` against repetition loops |
| vosk | Kaldi vosk-model-ru-0.42 / en-us-0.22 | once per language (ru, en) | CPU | models fetched on first use; about real time |
| gigaam | GigaAM-v3 e2e_rnnt (Sber) | Russian only | GPU (MPS) | see below |
| parakeet | parakeet-tdt-0.6b-v3 (parakeet-mlx) | any of 25 European, not forced | GPU | its own 120 s chunking; in mixed runs it runs first and defines the regions |
| apple | macOS SpeechTranscriber | once per supported language | Neural Engine | no Russian, so skipped for ru |
| turbo | whisper-large-v3-turbo | forced, once per language | GPU | backup for whisper |

**GigaAM** refuses audio longer than 25 s, so [lib/gigaam_run.py](lib/gigaam_run.py) cuts the audio
itself: measure the loudness of every 30 ms frame, cut at the quietest frame 14–22 s after the
previous cut, repeat; silent chunks are skipped and each chunk is transcribed on its own. In mixed
runs it cuts each Russian region separately, so no chunk crosses a language switch. A chunk that fails
is skipped; if all of them fail the engine counts as failed.

**Vosk** ([lib/vosk_run.py](lib/vosk_run.py)) streams the audio in 0.5 s pieces and stores a segment
each time Kaldi closes an utterance.

**Parallel runs.** `-j N` runs up to N jobs at a time (default 1, one after another). The queue is
ordered so GPU and CPU work interleave: whisper (GPU) · vosk (CPU) · frames + OCR (CPU) · gigaam ·
parakeet · apple · turbo. So `-j 2` already overlaps the slowest GPU and CPU jobs, and `-j 3` adds
the frames. With `-j > 1` each job's output goes to `logs/<job>.log` and the terminal only shows
start / done / failure (with the last lines of the log). In mixed runs parakeet and the region split
run before the queue, because GigaAM and whisper need the regions. Ctrl-C stops every running job.

A failed job does not stop the run: the rest is merged, the failures are listed at the end and vidx
exits with status 3.

Memory is the limit: whisper large-v3 needs about 4 GB, Vosk ru about 6 GB of RAM, parakeet and turbo
2–3 GB each, GigaAM about 1 GB. On a 24 GB Mac `-j 3` is comfortable, on 16 GB use `-j 2`.

### 5. Frames and OCR (job queue)

One job, [lib/frames.py](lib/frames.py) and [lib/ocr.swift](lib/ocr.swift):

1. **Capture**: a single ffmpeg pass keeps a frame when the scene changes by more than `--scene`
   (0.3) or when `--every` seconds (10) passed since the last kept frame. ffmpeg's `showinfo`
   gives each frame's timestamp. The raw frames go to a temporary folder.
2. **Dedup**: each frame becomes a 240×135 grey thumbnail and is compared with the last 6 kept
   frames, so a slide that comes back later is not kept twice. It is a duplicate when the mean pixel
   difference is below `--dedup` (6) **and** fewer than 1 % of pixels changed by more than 25
   levels. The second test keeps small text changes on white slides, which barely move the mean.
   `--roi 0.75` compares only the left ¾ of the frame (ignores Google Meet participant tiles).
3. **Crop** (`--crop`, or `--meet` = `--roi 0.75 --crop`): cut the frame to the bounding box of
   non-dark pixels, which is the shared screen.
4. **Save**: `frames/unique/HH-MM-SS.jpg` (`_1`, `_2` … for several frames in one second), contact
   sheets of 20 frames in `frames/sheets/`, and `frames/frames.json` (file → timestamp) last.
5. **OCR**: Apple Vision (accurate mode, ru + en, language correction) on every kept frame →
   `ocr/ocr.jsonl`. No model download, about 0.2 s per frame. New frames always mean new OCR.

### 6. Merge

[lib/merge.py](lib/merge.py) reads the `asr/*.json` files made by this run (files from earlier runs
with other options are ignored), `subs/*.json3`, the OCR results and `info.json`.

Every source has a language: `gigaam` = ru, `parakeet` = any, `<engine>@<lang>` and
`youtube-<kind>@<lang>` = that language, other engines = the run's only language.

**`aligned.md`**: the timeline is cut into 40 s windows. Each segment goes into the window that
contains its midpoint (so no segment appears twice), consecutive repeats are dropped, and each window
lists every source's text plus the OCR text of the frames shown in that window. Use it to check one
engine against another.

**`transcript.md`** is built region by region (the whole file is one region in a normal run):

1. **Base**: the first source whose language fits the region, in the order GigaAM, whisper,
   parakeet, turbo, apple, YouTube manual, YouTube auto, Vosk. Its segments with the midpoint inside
   the region are taken in order, consecutive repeats dropped.
2. **Cut**: segments starting inside a `--cut` range are dropped (private talk, breaks).
3. **Fallback**, per base segment: if it looks like the engine skipped speech, it is replaced with
   the longer of parakeet / whisper (same language) for the same span, counting only their segments
   that lie at least 60 % inside it. "Skipped speech" means all of: fewer than 6 characters per
   second (fillers not counted), the alternative is more than twice as long and longer than 80
   characters, and the alternative has at most 25 characters per second (otherwise it is probably a
   repetition loop).
4. **Clean**: remove hesitation sounds (а-а, э-э, ммм, хм, uh, um), apply the glossary
   replacements, capitalise sentences.
5. **Layout**: all regions in time order, ~55 s paragraphs with a timestamp, YouTube chapters as
   headings, and a header with the languages, the number of regions, chunks per source and
   language, and the fallback count.

Text that comes from outside (YouTube titles, chapters, subtitles, OCR) is escaped before it goes
into Markdown, so it cannot add images, links or HTML to the preview.

Also written: `transcript.txt` (one `[HH:MM:SS] text` line per chunk, not escaped) and `ocr.md`
(on-screen text per frame).

### `vidx find`

[lib/find.py](lib/find.py) runs a case-insensitive regex over every engine's segments and the OCR
lines and prints `HH:MM:SS  source  text`, so you can jump to a phrase in the video. A third
argument limits it to one source (`gigaam`, `whisper-large-v3@en`, `ocr`, …).

## Glossary

`-g FILE`, example [glossaries/example.txt](glossaries/example.txt):

```
prompt: Созвон команды разработки: Kubernetes, Helm, Kafka, PostgreSQL, Grafana, …
<W>[Кк]афк(а|и|е|у|ой)<E> => Kafka
\b(Graphana|Grafanna)\b => Grafana
```

- `prompt:` is passed to whisper as `--initial-prompt`. It teaches the spelling of terms.
- `regex => replacement` lines are applied to the merged transcript (Python `re`). `<W>` and `<E>`
  are word boundaries that also work for Cyrillic.

Only `example.txt` is tracked by git; put your own glossaries next to it.

## Output folder

| Path | Content |
|---|---|
| `transcript.md`, `transcript.txt` | merged transcript |
| `aligned.md` | every source and OCR side by side, 40 s windows |
| `ocr.md` | on-screen text per frame |
| `asr/<engine>[@lang].json` | raw engine output with timestamps |
| `subs/{manual,auto}.<lang>.json3` | YouTube subtitles |
| `frames/unique/`, `frames/sheets/`, `frames/frames.json` | pictures |
| `ocr/ocr.jsonl` | OCR text per frame |
| `lang.txt`, `regions.json` | detected languages, language regions (mixed runs) |
| `logs/` | per-job logs (`-j > 1`) |
| `.vidx/` | marker, option stamps, input, failed jobs |
| `audio.wav`, `source.*`, `info.json`, `description.txt`, `thumbnail.jpg` | inputs |

Exit status: 0 everything worked, 3 some jobs failed (the rest is merged), 1 usage or setup error,
130 interrupted.

## Installation

`vidx setup` installs or upgrades, then downloads every model once. Re-running it is safe.

| Where | What |
|---|---|
| Homebrew | ffmpeg, deno (yt-dlp needs a JS runtime for YouTube), uv |
| `uv tool` | mlx-whisper 0.4.3, parakeet-mlx 0.5.3, yt-dlp (latest: YouTube breaks old versions) |
| `.venv` (Python 3.14) | vosk, pillow, numpy (pinned) |
| `.venv-giga` (Python 3.13) | GigaAM at a pinned commit, torch, torchaudio, soundfile (pinned) |
| `lib/` | `ocr`, `apple_asr` compiled from Swift |
| `models/` | vosk-model-ru-0.42 (the English model is fetched on first use) |
| Hugging Face cache | whisper-large-v3, whisper-large-v3-turbo, parakeet-tdt-0.6b-v3 |
| `~/.cache/gigaam` | GigaAM v3_e2e_rnnt |

The Python code runs on 3.11–3.14. GigaAM needs onnxruntime 1.23, which has no Python 3.14 build, so
its venv uses 3.13. `VIDX_PYTHON` / `VIDX_PYTHON_GIGA` override the versions; delete `.venv` or
`.venv-giga` to rebuild one. The script runs under bash 5 and the macOS `/bin/bash` 3.2.

Models are downloaded from their publishers and come under their own licenses; check them before
using the output commercially. The Vosk zips are fetched over HTTPS without a checksum (none is
published).

The network is used only by yt-dlp, the first model downloads and a short Hugging Face update check
on each run (`HF_HUB_OFFLINE=1` skips it).

## Files

```
vidx                  entry point (bash): options, download, audio, language, job queue
lib/common.py         helpers shared by the Python scripts
lib/detect_lang.py    spoken-language detection on 10 s clips
lib/regions.py        mixed speech → single-language regions
lib/gigaam_run.py     GigaAM on pause-cut chunks
lib/vosk_run.py       Vosk
lib/apple_asr.swift   Apple SpeechTranscriber (+ --locales)
lib/frames.py         frame dedup, crop, contact sheets
lib/ocr.swift         Apple Vision OCR
lib/merge.py          aligned.md, transcript.md/.txt, ocr.md
lib/find.py           phrase → timecode
glossaries/example.txt  glossary format
```
