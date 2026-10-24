"""vidx help texts. ${NAME} placeholders are filled by texts.render() (string.Template)."""

USAGE = """\
vidx — extract pictures and text from a video file or a YouTube / any yt-dlp URL.
Runs every local speech recognizer, OCRs the screen, and merges it all into one
transcript. Everything runs on ${MACHINE}; nothing is uploaded.

USAGE
  vidx <file|url> [options]           extract (default command)
  vidx find <out-dir> <regex> [src]   find the timecode of a phrase
  vidx setup                          install tools and download models (~13 GB)
  vidx help [run|find|setup]          this help, or help for one command

QUICK START
  vidx setup
  vidx ~/Downloads/meeting.mp4 --meet -j 3
  vidx "https://www.youtube.com/watch?v=VIDEO_ID" -g ${VIDX_DIR}/glossaries/example.txt
  vidx find meeting_vidx-out 'kubernetes|helm'

Run "vidx help run" for all options, outputs and the glossary format.
"""

RUN = """\
vidx <file|url> [options]

Pipeline. A step is skipped when its output exists and was made with the same
options; -f redoes everything. Full description: ${VIDX_DIR}/README.md
  1. source     local file (symlinked) or yt-dlp download + info, description,
                thumbnail, manual and auto subtitles (YouTube)
  2. audio      ffmpeg → audio.wav, 16 kHz mono
  3. language   given with -l, or detected on 10 s clips (1 per minute)
  4. speech     every engine that fits the language   ┐ one job queue,
  5. pictures   frames → dedup → contact sheets → OCR ┘ -j N at a time
  6. merge      aligned.md (all sources side by side) + transcript.md (merged)

INPUT / OUTPUT
  -o, --out DIR          output dir (default ./<file name | video id>_vidx-out).
                         An existing non-empty dir not made by vidx is refused.
  -l, --lang LIST        auto (default) | ru | en | ru,en | uk,en,de | any
                         whisper language codes
      --max-height N     YouTube download resolution cap (default 1080)
      --sub-langs LIST   YouTube subtitle tracks (default: ru, en, --lang
                         languages and the video's language, originals only)
  -f, --force            redo every step, re-download the video and subtitles

LANGUAGE
  auto     whisper turbo detects 10 s clips, one per minute (5..30). A language
           counts if it wins at least 2 clips and 20 % of them (any number of
           languages), so auto finds only languages with long passages: pass
           -l for short switches. No speech at all → ru. Cached in lang.txt.
  ru       one language: whisper, turbo, Apple and Vosk are told it, GigaAM
           runs (ru only), parakeet is never told a language and runs only
           for its 25 European languages (elsewhere it writes gibberish).
  ru,en    mixed speech (whole passages in each language), any number of
  uk,en,de languages, also with the same script (ru,uk  de,fr). The audio is
           cut at pauses into 2-8 s pieces, whisper turbo identifies the
           language of each (among the listed ones), short or unsure pieces
           follow their neighbours, regions are at least 3 s → regions.json.
           parakeet runs first if it knows every listed language, and the
           boundaries snap to its sentence edges. whisper, turbo, Vosk and
           Apple run once per language that got a region (whisper/turbo on
           its regions only), GigaAM on the ru regions only; output files get
           @lang (whisper-large-v3@en). Without this whisper would translate
           and GigaAM would garble the other language. If the split fails, the
           run continues with the first language only.
           Russian with English jargon is NOT mixed: use -l ru.

SPEECH
  -e, --engines LIST     comma list of whisper,parakeet,gigaam,turbo,apple,vosk
                         (default: all)
  -j, --jobs N           run N jobs at once (default 1). Queue order:
                           whisper (GPU) · vosk (CPU) · frames+OCR (CPU) ·
                           gigaam · parakeet · apple · turbo
                         so -j 2..3 keeps both GPU and CPU busy. Job output
                         then goes to logs/<job>.log. RAM: whisper ~4 GB,
                         vosk ru ~6 GB, others 1-3 GB; on a 16 GB ${MACHINE_SHORT} use -j 2
  -g, --glossary FILE    domain terms, see GLOSSARY below
      --cut A-B          drop a time range from transcript.md/.txt, e.g.
                         --cut 36:30-50:00 (repeatable; [h:]m:s or seconds)

${ENGINE_TABLE}  Other models: VIDX_MODEL_WHISPER, VIDX_MODEL_TURBO (also language ID),
  VIDX_MODEL_PARAKEET, VIDX_VOSK_<LANG> (e.g. VIDX_VOSK_RU=vosk-model-small-ru-0.22).

  Merge, per language region: base = first fitting source of GigaAM, whisper,
  parakeet, turbo, apple, YouTube, Vosk. A base chunk is swapped for parakeet/
  whisper text (whisper/turbo/apple where parakeet does not know the language)
  only if it looks like speech was skipped: below the language's chars/s floor
  (6 for ru/en, 2 for zh), the other is >2x longer, longer than 5 s of normal
  speech (80 chars ru/en, 22.5 zh) and not faster than the loop limit (25 ru/en).

PICTURES
      --scene X          ffmpeg scene-change threshold 0..1 (default 0.3)
      --every SEC        also grab a frame after SEC s without one (default 10)
      --all-frames       look at every frame instead (a frame shown for 1/25 s);
                         ffmpeg drops repeats, then --dedup; slower
      --dedup X          mean pixel diff on a 240x135 grey thumbnail below which
                         a frame is a duplicate (default 6), unless >1 % of the
                         pixels changed (small text on white slides)
      --roi F            compare only the left fraction F of the frame (def. 1)
      --crop             crop frames to the non-dark screen-share area
      --meet             Google Meet recordings: --roi 0.75 --crop
                         (ignores the participant tiles on the right)
  OCR (${OCR_NAME}) reads the run's languages plus English (be: ru + uk), one
  pass per script (Latin/Cyrillic, CJK, Arabic, Devanagari...).

SKIP STEPS
      --no-asr           no audio, language or speech: pictures + OCR (+ subs)
      --no-frames  --no-ocr  --no-subs

EXIT STATUS
  0 all jobs succeeded · 3 some jobs failed (the rest is merged anyway; see the
  summary and logs/) · 1 usage or setup error · 130 interrupted

OUTPUT FILES (in the output dir)
  transcript.md          merged transcript, 55 s paragraphs, YouTube chapters
  transcript.txt         same text as plain text (not escaped for Markdown),
                         one [HH:MM:SS] line per chunk
  aligned.md             every source + OCR side by side in 40 s windows
  ocr.md                 on-screen text per frame
  asr/<engine>[@lang].json           raw engine output with timestamps
  subs/{manual,auto}.<lang>.json3    YouTube subtitles
  frames/unique/HH-MM-SS.jpg         deduplicated frames
  frames/sheets/sheet-NN.jpg         contact sheets, 20 frames each
  frames/frames.json, ocr/ocr.jsonl  frame → timestamp, frame → text
  lang.txt, regions.json             detected languages, language regions
  logs/<job>.log                     per-job output when -j > 1
  audio.wav, source.*, info.json, description.txt, thumbnail.jpg
  .vidx/                             marker + option stamps of each step

GLOSSARY (-g FILE), see ${VIDX_DIR}/glossaries/example.txt
  prompt: <text>            whisper --initial-prompt (spelling of terms)
  <regex> => <replacement>  applied to the merged transcript (Python re;
                            <W> and <E> are Cyrillic-aware word boundaries)
  # comment

EXAMPLES
  vidx call.mp4 --meet -g my-terms.txt --cut 36:30-50:00 -j 3
  vidx call.mp4 -e gigaam,whisper,parakeet       # skip the slow ones
  vidx talk.mp4 --no-asr --scene 0.2             # pictures + OCR only
  vidx "https://youtu.be/VIDEO_ID" -l ru,en -j 2 # Russian + English talk
"""

FIND = """\
vidx find <out-dir> <regex> [source]

Prints "HH:MM:SS  source  text" for every segment of every engine (and every
OCR line) matching the case-insensitive Python regex. Restrict to one source
with its file stem from asr/, e.g. gigaam, whisper-large-v3, parakeet.

  vidx find meeting_vidx-out 'kubernetes|helm'
  vidx find meeting_vidx-out 'deadline' gigaam
"""


def render(text, **values):
    """Fill the ${NAME} placeholders of a help text."""
    from string import Template

    return Template(text).substitute(values)


# The engine table of `vidx help run`, per platform
ENGINE_TABLE = {"darwin": """\
  engine    model                        languages          runs on  speed*
  whisper   whisper-large-v3 (MLX)       99, forced         GPU      ~4 min
  parakeet  parakeet-tdt-0.6b-v3 (MLX)   25 European        GPU      ~1 min
  gigaam    GigaAM-v3 e2e_rnnt (Sber)    ru only            GPU      ~3 min
  turbo     whisper-large-v3-turbo       99, forced         GPU      ~2 min
  apple     macOS SpeechTranscriber      en de fr it es     ANE      ~1 min
                                         ja zh hi; no ru
  vosk      Kaldi vosk-model-<lang>      ru en de fr it es  CPU      ~60 min
                                         ja zh hi uk ar
  youtube   manual + auto subtitles      as published       —        download
  * rough, per hour of audio on Apple Silicon, one job at a time
  apple downloads its speech model per locale on first use; the download
  gets VIDX_APPLE_TIMEOUT seconds (default ${APPLE_TIMEOUT_DEFAULT}, at most 86400), then
  the engine fails.
""", "portable": """\
  engine    model                        languages          runs on
  whisper   whisper-large-v3             99, forced         CUDA or CPU (faster-whisper)
  parakeet  parakeet-tdt-0.6b-v3         25 European        CUDA, DirectML or CPU (onnx-asr)
  gigaam    GigaAM-v3 e2e_rnnt (Sber)    ru only            CUDA or CPU (PyTorch)
  turbo     whisper-large-v3-turbo       99, forced         CUDA or CPU (faster-whisper)
  apple     macOS SpeechTranscriber: not available on this platform (skipped)
  vosk      Kaldi vosk-model-<lang>      ru en de fr it es  CPU
                                         ja zh hi uk ar
  youtube   manual + auto subtitles      as published       —
  vidx setup reports the device of each engine. Without a GPU whisper large-v3 is
  slow: VIDX_MODEL_WHISPER=large-v3-turbo trades some accuracy for speed.
"""}
