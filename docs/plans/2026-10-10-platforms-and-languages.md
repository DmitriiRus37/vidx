# vidx: macOS + Linux + Windows, 12 languages, N-language mixed speech: implementation plan

Status: Phase 0 (without the CI of 0.2), Phase 1 and the language part of Phase 4 are built (branch
`languages`, 2026-10-11); where the build differs from the plan, the step says so under **As built**.
Phases 0.2, 2 and 3 and the validation part of Phase 5 were refined and built on branch `porting`: see
`2026-10-11-porting-analysis.md` (its section 6 lists the deviations from this plan, section 10 what was
validated where). Written 2026-10-10 from a probe run on the current code (commit
`bfb143c`).

## Goals

1. **Three equal platforms**: macOS (Apple Silicon), Linux (x86_64/arm64, NVIDIA GPU or CPU) and
   Windows (x86_64, NVIDIA GPU or CPU), natively — no WSL requirement. Same CLI, same output files,
   same tests on all three.
2. **Languages**: ru, en (today) plus de, fr, it, es, ja, zh, hi, be, uk, ar — single-language videos
   at the quality bar of today's Russian (several engines + merge, not "only whisper runs").
3. **Mixed speech with any number of languages**: `-l uk,en,de` (3+) and same-script pairs
   (`-l ru,uk`, `-l de,fr`).

Non-goals: cloud engines; GPU acceleration on AMD/Intel GPUs for whisper (CPU fallback there);
an installer/GUI.

## Probe findings (evidence for this plan)

Test clips: macOS TTS voices, ~25 s per language, plus `mix3` = Ukrainian 6.4 s → English 5.2 s →
German 4.6 s. Run with today's vidx, `-e whisper,parakeet,turbo,gigaam -j 2`.

| Lang | auto-detect | today's transcript | whisper chars/s | Notes |
|---|---|---|---|---|
| de, fr, it, es | ✓ | ✓ correct | 18–21 | parakeet supports them |
| uk | ✓ | ✓ correct | 13.8 | parakeet supports it |
| hi | ✓ | ✓ correct | 15.4 | parakeet → romanised gibberish (unused here) |
| ar | ✓ | ✓ correct | 8.0 | parakeet → gibberish (unused here) |
| ja | ✓ | ✓ correct, **by luck** | 6.6 | 6.6 is just above SKIP_CPS = 6 |
| zh | ✓ | **✗ parakeet gibberish** ("Sin Tian Woman Taulu…") | 4.5 | < 6 → fallback to parakeet |
| be | — | not tested (no TTS voice) | — | only whisper/turbo support it |
| mix3 uk+en+de | — | **✗ one "uk" region**, en/de engines skipped | — | script split = Cyrillic vs "other" |

Engine coverage (from model cards / API queries):

| Lang | whisper, turbo | parakeet v3 | GigaAM | Apple ASR | Vosk model | Apple Vision OCR |
|---|---|---|---|---|---|---|
| ru, en | ✓ | ✓ | ru | en | ✓ | ✓ |
| de, fr, it, es | ✓ | ✓ | – | ✓ | ✓ (1.2–4.4 GB) | ✓ |
| uk | ✓ | ✓ | – | – | ✓ (0.3 GB) | ✓ |
| ja, zh | ✓ | – | – | ✓ | ✓ (1–1.5 GB) | ✓ |
| hi | ✓ | – | – | ✓ (hi-IN) | ✓ (1.5 GB) | ✓ |
| ar | ✓ | – | – | – | ✓ (0.3–1.3 GB) | ✓ (ar-SA) |
| be | ✓ | – | – | – | – | – (use ru+uk) |

Other findings:

- **Bug**: `apple_asr de` resolves to `de_AT` and the model download hangs (no timeout).
- **Bug**: parakeet is tagged "any language", so it is a fallback for ja/zh/hi/ar/be, where it
  outputs gibberish.
- `merge.md()` escapes every `!`, so transcripts show `усім\!`; only `![` / `[..](` need escaping.
- GigaAM's multilingual models document only ru/kk/ky/uz/en and have no punctuation: not useful here.
- macOS-only today: MLX engines, `apple_asr`, `ocr` (Swift), Homebrew, `.venv/bin/python` paths, the
  macOS font path, and bash itself on Windows.
- Cross-platform replacements exist:
  - **faster-whisper** (CTranslate2; Linux/Windows; CUDA or CPU int8): whisper large-v3 / turbo,
    prompt, `condition_on_previous_text`, language detection.
  - **onnx-asr** (onnxruntime; Windows/Linux/macOS; CPU, CUDA, DirectML, CoreML): parakeet-tdt-0.6b-v3
    and GigaAM v3 e2e, token timestamps, VAD for long audio.
  - **Vosk**, **ffmpeg**, **yt-dlp**, **PyTorch GigaAM**: already cross-platform.
  - OCR for Linux/Windows: **RapidOCR** (PaddleOCR models on onnxruntime; latin, cyrillic, arabic,
    devanagari, CJK).

## Estimate

One developer, working days, including tests:

| Phase | Days |
|---|---|
| 0. Test harness + CI on 3 OSes | 2–3 |
| 1. Language bugs and per-language config (no platform change yet) | 2–3 |
| 2. Python orchestrator replacing the bash entry point | 4–5 |
| 3. Engine backends for Linux/Windows | 4–6 |
| 4. N-language mixed speech | 3–4 |
| 5. Real-hardware validation, docs, release | 2–3 |
| **Total** | **17–24 days (≈ 4–5 weeks)** |

The biggest risks: the CUDA setup on Windows (driver/cuDNN versions) and calibrating per-script thresholds
without real recordings (TTS speech is cleaner than meetings). Belarusian quality depends entirely on
whisper.

## Order and why

```
0 harness + CI ─► 1 language fixes ─► 2 Python orchestrator ─► 3 Linux/Windows engines ─► 4 N languages ─► 5 validate
```

- Tests and the 3-OS CI come first, so every later step is proven on all platforms as it lands.
- Phase 1 fixes real bugs on the current code (small, high value) before the big rewrite.
- Phase 2 must precede Phase 3: Windows cannot run the bash entry point.
- Phase 4 builds on the engine interface from Phase 3.

## Conventions for every step

- **TDD**: write the failing test (red), make it pass with the smallest change (green), refactor.
- One step = one commit, message `<area>: <what>`; all tests green on the 3-OS CI before the next step.
- Tests never download big models. They use:
  - **fake engines**: stub scripts that write fixed JSON;
  - **fixtures**: short WAV/MP4 clips in `tests/fixtures/`, made with macOS TTS and committed;
  - **tiny models** (whisper `tiny`, ~75 MB) only in the nightly "smoke" job.
- Each step is described as **AS IS** (current behaviour) → **TO BE** (target), then **Test first**
  and **Done when**.

---

## Phase 0. Test harness and CI (2–3 days)

### 0.1 Python test runner

- **AS IS**: no tests; checks were manual runs.
- **TO BE**: `tests/` with pytest; `uv run --with pytest pytest` runs them; `pyproject.toml` holds the
  tool config (ruff, pytest).
- **Test first**: `tests/test_smoke.py::test_lib_imports` imports `lib/common.py` and asserts `hms(3725)
  == "01:02:05"`.
- **Done when**: green locally on macOS.

### 0.2 CI on three operating systems

- **AS IS**: no CI.
- **TO BE**: GitHub Actions matrix `macos-14` (arm64), `ubuntu-24.04`, `windows-2025`; Python 3.11 and
  3.14; runs ruff + pytest. Nightly job (later steps) adds tiny-model smoke tests.
- **Test first**: the 0.1 test, run in CI.
- **Done when**: green on all 3 OSes.

### 0.3 Characterisation tests for the merge

- **AS IS**: merge behaviour is only described in the README.
- **TO BE**: golden tests that pin today's behaviour: fixture `asr/*.json` (copied from the ru+en test
  run) → expected `transcript.txt`.
  - one case for the fallback rule;
  - one for `--cut`;
  - one for regions;
  - one for consecutive-repeat removal.
- **Test first**: these tests *are* the step; they must pass on current code.
- **Done when**: green; changing a threshold in merge.py makes a test fail.

### 0.4 Pure functions out of the scripts

- **AS IS**: `frames.py`, `regions.py`, `detect_lang.py` are top-level scripts; their logic cannot be
  imported.
- **TO BE**: each exposes testable functions:
  - `frames.duplicate(sig_a, sig_b, thr)`;
  - `regions.label_sentences(...)`, `regions.merge_pieces(...)`;
  - `detect_lang.vote(counter, n)`.
  
  `main()` stays the CLI.
- **Test first**: unit tests for each:
  - white slide with a small text change → not a duplicate;
  - 4 votes ru + 1 en of 5 → `ru`;
  - unlabelled gap split between neighbours.
- **Done when**: green; scripts still produce identical output on the fixtures.

### 0.5 Fake engines and an end-to-end CLI test

- **AS IS**: the only way to test the pipeline is with real models (minutes, GBs).
- **TO BE**: `VIDX_FAKE_ENGINES=1` makes vidx call `tests/fakes/<engine>` stubs that write canned
  segments after a configurable delay.
- **Test first**: `tests/test_cli.py`:
  - `vidx fixture.mp4 -l ru` → `transcript.md` exists, exit 0;
  - with a failing fake → exit 3 and the failure listed;
  - second run → every job "cached".
- **Done when**: green on macOS and Linux (bash). Windows is skipped until Phase 2.

### 0.6 CLI contract tests

- **AS IS**: option validation was tested by hand (see the review session).
- **TO BE**: table-driven tests run against the bash vidx and later against the Python one:
  - missing `-g` file → exit 1 + message;
  - bad `--cut` → exit 1;
  - unknown engine → exit 1;
  - foreign `-o` dir → refused;
  - no audio track → message;
  - `--no-asr` on a video without audio → exit 0.
- **Done when**: green. These become the acceptance tests for Phase 2.

## Phase 1. Language bugs and per-language config (2–3 days)

### 1.1 One language table

- **AS IS**: language knowledge is scattered:
  - `vosk_model()` handles ru/en only;
  - `OCR_LANGS=ru-RU,en-US`;
  - GigaAM is hard-coded as `ru`;
  - parakeet is `any`;
  - fillers and capitalisation are Russian-only.
- **TO BE**: `lib/languages.py` maps each of ru, en, de, fr, it, es, ja, zh, hi, be, uk, ar to:
  - `script`: cyrillic, latin, cjk, arabic or devanagari;
  - `apple_locale` (de → de-DE, zh → zh-CN, hi → hi-IN…);
  - `ocr_langs`;
  - `vosk_model`;
  - `parakeet`: bool;
  - `joiner` (" " or "");
  - `fillers` regex;
  - `skip_cps`, `loop_cps`.
  
  Unknown codes get whisper-only defaults for their script. The bash script reads it via
  `python -m languages <code> <field>`.
- **Test first**:
  - every listed language has every field;
  - `parakeet` is true exactly for its 25 languages;
  - an unknown code (`ko`) → defaults, not a crash.
- **Done when**: green; no behaviour change for ru/en (Phase 0 goldens still green).

### 1.2 parakeet only for its languages

- **AS IS**: parakeet is "any language"; on zh the merge took its gibberish (probe, zh row).
- **TO BE**: parakeet's language set = its 25 languages. Elsewhere it is not queued and not a
  fallback.
- **Test first**: golden zh fixture (whisper 4.5 chars/s + parakeet gibberish) → transcript is the whisper
  Chinese text.
- **Done when**: green; ja/zh/hi/ar/be runs no longer start parakeet.
- **As built**: with parakeet gone, a ja/zh/hi/ar/be region had no fallback at all (the alternatives were
  parakeet and whisper, and whisper is the base). Where parakeet does not know the region's language
  the alternatives are whisper, turbo and Apple (if it has the locale).

### 1.3 Speech-rate thresholds per script

- **AS IS**: `SKIP_CPS = 6`, `LOOP_CPS = 25` for all languages; correct Chinese runs at 4.5 chars/s, Japanese at
  6.6.
- **TO BE**: thresholds from the language table:
  - latin and cyrillic 6 / 25;
  - arabic 3 / 16;
  - devanagari 5 / 25;
  - cjk 1.5 / 10.
  
  Calibrated on the probe clips (8.0, 15.4, 4.5–6.6 chars/s), with 2–3× headroom.
- **Test first**: per script, a "normal speech" chunk is not replaced and a chunk at ⅓ of the normal rate
  is.
- **Done when**: green; ru/en goldens unchanged.
- **As built** (skip / loop chars/s, in `lib/languages.py`):
  - latin and cyrillic 6 / 25, but de, fr, it, es 7 / 30: their TTS speech ran at 18–21 chars/s, so 25
    is too close to normal for a loop limit and 6 below ⅓ of it;
  - arabic 3 / 16 (normal 8.0);
  - devanagari 6 / 25, not 5: Hindi ran at 15.4, like Latin, so ⅓ of it is 5.1 and needs a floor above;
  - cjk 2 / 10, ja 2.5 / 12: ⅓ of Chinese's 4.5 is 1.5, exactly the planned floor, which would not
    catch it; Japanese runs faster (6.6);
  - hangul 3 / 16 and thai, lao, myanmar, khmer 6 / 25 (joiner "", no word spaces): not calibrated,
    no clip.

  Rule used: skip 2.2–3× below the normal TTS rate, loop 1.4–2× above it. The "alternative must be
  longer than 80 chars" rule became 5 s of the script's normal speech (`normal_cps`: Latin/Cyrillic 16,
  so still 80; CJK 4.5 → 22.5; Arabic 8; Devanagari 15): 80 chars is 18 s of Chinese, so no realistic
  5–10 s chunk qualified. TTS speech is cleaner and steadier than meetings; recalibrate on real
  recordings (Phase 5).

### 1.4 Joining and capitalisation per script

- **AS IS**: chunks are joined with spaces (`" ".join`); capitalisation only knows `[а-яёa-z]`.
- **TO BE**: joiner from the table ("" for ja/zh). Capitalisation uses `str.isalpha()` / `str.upper()`,
  so і/ї/є/ґ/ў/ä/é work; no-op for scripts without case.
- **Test first**:
  - zh chunks joined without spaces;
  - "привіт. їжак" → "Привіт. Їжак";
  - "über. äpfel" → "Über. Äpfel".
- **Done when**: green.

### 1.5 Fillers per language

- **AS IS**: only Russian/English hesitation sounds are removed.
- **TO BE**: per-language filler regex:
  - de: äh, ähm;
  - fr: euh;
  - es/it: eh, ehm;
  - uk/be: е-е, ем;
  - ja: えー, あの(ー);
  - zh: 嗯, 呃;
  - ar: آآ.
  
  Applied by the language of the region.
- **Test first**: per language, a sentence with fillers → without; real words that contain the same
  letters stay.
- **Done when**: green.

### 1.6 Markdown escaping: less aggressive

- **AS IS**: `md()` escapes every `! [ ] * _ |`, so raw transcripts show `усім\!`.
- **TO BE**: escape only what can inject:
  - `<` and `>`;
  - `![`;
  - `[text](`;
  - a leading `#`, `>` or `-`;
  - backticks.
- **Test first**: `"усім!"` unchanged; `"![x](http://t)"` neutralised; `"<img src=x>"` escaped.
- **Done when**: green; goldens updated deliberately.
- **As built**: `[text](` was not enough: nested brackets (`[click [here]](url)`) and a backslash
  (`\[x](url)`, which turns the added escape into a literal backslash) still made links, and a line break
  in a title (`Intro\n# Injected`) started a block. Every `[` with any later `](`, `][` or `]:` is
  escaped, backslashes are doubled, line breaks become spaces. Tests render with a CommonMark parser
  (markdown-it-py) and require the same plain text back.

### 1.7 Apple engine: explicit locales and a timeout

- **AS IS**: `apple_asr de` picked `de_AT` and the model download hung for minutes.
- **TO BE**:
  - vidx passes the table's `apple_locale` (de-DE);
  - `apple_asr` gets `--timeout` (default 300 s for the asset download), exits 4 on timeout;
  - vidx marks the engine failed and continues.
- **Test first**:
  - unit: table maps de → de-DE;
  - integration (macOS only): `apple_asr --locales` contains every mapped locale.
- **Done when**: green on macOS CI; skipped elsewhere.
- **As built**: the timeout is 1..86400 s, checked by vidx (`VIDX_APPLE_TIMEOUT`) and by `apple_asr`
  (inf or 1e12 would trap converting to nanoseconds); the default 300 lives only in vidx, `apple_asr`
  requires `--timeout`.

### 1.8 OCR and Vosk follow the run's languages

- **AS IS**: OCR always uses ru-RU,en-US; Vosk only has ru/en models.
- **TO BE**:
  - OCR languages = union of the table's `ocr_langs` for the run's languages, plus en;
  - be → ru-RU, uk-UA;
  - Vosk models from the table (de-0.21, fr-0.22, it-0.22, es-0.42, ja-0.22, cn-0.22, hi-0.22,
    uk-v3, ar-mgb2-0.4), still fetched on first use;
  - no Vosk for be.
- **Test first**:
  - `-l ja` → OCR request includes ja-JP;
  - `-l be` → no Vosk job, log line says why.
- **Done when**: green (fake engines); one manual run with a real ja slide confirms Japanese OCR.
- **As built**: one union was wrong: Vision reads only the script of a request's first language (plus
  Latin), so `ru-RU,zh-Hans,en-US` read nothing of a Chinese slide. OCR runs one request per script group
  (Latin/Cyrillic first, CJK together, Arabic, Devanagari…; en-US in each) and merges their lines by
  position, a later group adding only lines no earlier one read (`ru-RU,en-US;zh-Hans,en-US`).

## Phase 2. Python orchestrator (4–5 days)

The bash entry point cannot be a first-class citizen on Windows. Replace it with a Python package that
has the same CLI and passes the Phase 0 contract and end-to-end tests.

### 2.1 Package skeleton

- **AS IS**: `vidx` is a 640-line bash script; `lib/*.py` are loose scripts.
- **TO BE**: `src/vidx/` package with:
  - a `pyproject.toml` console script `vidx`;
  - install with `uv tool install git+https://github.com/DmitriiRus37/vidx`;
  - `lib/` modules moved into the package.
- **Test first**: `vidx --help` via the console script prints the usage; `python -m vidx --help` too.
- **Done when**: green on 3 OSes.

### 2.2 Options and validation

- **AS IS**: parsed in bash.
- **TO BE**: `argparse` with the same flags, defaults and messages.
- **Test first**: the Phase 0.6 contract table, now run against the Python CLI.
- **Done when**: contract tests green on 3 OSes.

### 2.3 Output folder, stamps, input tracking

- **AS IS**: `.vidx/` marker, `*.params` stamps, `input` file, `failed` list in bash.
- **TO BE**: same files, same semantics, in `vidx/state.py`. Paths via `pathlib`. No symlink for local
  input: Windows needs admin rights for symlinks, so store the absolute path in `.vidx/input` and read
  the file from there.
- **Test first**:
  - option change → step reruns;
  - same options → cached;
  - foreign folder → refused;
  - different input → everything redone.
- **Done when**: green on 3 OSes.

### 2.4 Source: local file and yt-dlp

- **AS IS**: bash calls yt-dlp four times with `--ignore-config --no-playlist`, renames
  `info.info.json`.
- **TO BE**: same calls via `subprocess` (yt-dlp as a library is an option but the CLI keeps isolation),
  same file names, the `%`-escaping of the output template.
- **Test first**: yt-dlp with `--enable-file-urls file://fixture.mp4` (offline) → `source.mp4`,
  `info.json`, `description.txt` exist; id sanitised.
- **Done when**: green on 3 OSes.

### 2.5 Audio and language steps

- **AS IS**: ffmpeg to `audio.tmp.wav` + rename; `detect_lang.py` via mlx-whisper's Python.
- **TO BE**: same, with the detector chosen by the backend (Phase 3.2); until then macOS uses MLX and
  others use the fake.
- **Test first**: no audio track → exit 1 + message; `--no-asr` skips both.
- **Done when**: green.

### 2.6 Job queue

- **AS IS**: bash background jobs, `jobs -rp` polling, logs per job, exit 3 on failures.
- **TO BE**:
  - `concurrent.futures.ThreadPoolExecutor(max_workers=JOBS)`, each job a `subprocess.Popen`;
  - same queue order;
  - same `logs/<job>.log` names;
  - failure summary and exit 3.
- **Test first**: fake engines with delays:
  - `-j 2` never runs 3 at once;
  - order respected;
  - one failure → others complete, exit 3.
- **Done when**: green on 3 OSes.

### 2.7 Ctrl-C on every platform

- **AS IS**: bash trap + `kill_tree` via `pgrep` (POSIX only).
- **TO BE**: each engine runs in its own process group:
  - **POSIX**: `start_new_session=True`, killed with `os.killpg`;
  - **Windows**: `CREATE_NEW_PROCESS_GROUP`, killed with `psutil` (kill children, then the parent).
  
  Exit 130.
- **Test first**: start with a fake engine that sleeps 60 s, send SIGINT (POSIX) /
  `CTRL_BREAK_EVENT` (Windows) to vidx → exit 130 within 5 s, no fake process alive.
- **Done when**: green on 3 OSes.

### 2.8 Merge, find, help; retire bash

- **AS IS**: `vidx find`, `vidx help ...` in bash.
- **TO BE**:
  - Python subcommands with the same help text;
  - the bash `vidx` becomes a 3-line wrapper calling `python -m vidx`, then is removed one release
    later;
  - README updated (install via `uv tool install`).
- **Test first**: full Phase 0 end-to-end suite against the Python CLI.
- **Done when**: green on 3 OSes; macOS real-model run of the ru+en test video gives the same
  `transcript.txt` as the bash version.

## Phase 3. Engine backends for Linux and Windows (4–6 days)

### 3.1 Engine interface

- **AS IS**: engines are bash functions with different calling styles.
- **TO BE**: `Engine` protocol:
  - `name`;
  - `languages(platform)`;
  - `available()` (installed? platform?);
  - `command(audio, lang, clips, out)`.
  
  A registry picks the backend per OS; the queue builds jobs from the registry.
- **Test first**:
  - registry on "linux" lists faster-whisper, not mlx;
  - on "darwin" mlx;
  - an engine for an unsupported language is not queued;
  - `apple` is `available()` only on darwin.
- **Done when**: green; fake engines go through the same interface.

### 3.2 whisper on Linux/Windows: faster-whisper

- **AS IS**: mlx-whisper only (Apple GPU).
- **TO BE**: `faster-whisper` backend with:
  - device auto (CUDA if available, else CPU int8);
  - models large-v3 and large-v3-turbo;
  - `initial_prompt`, `condition_on_previous_text=False`;
  - `clip_timestamps` for regions;
  - `detect_language` for 3.x/4.x;
  - output in the same `{segments: [...]}` JSON.
  
  macOS keeps MLX (faster on Apple GPU).
- **Test first**: nightly smoke with the `tiny` model on the 5 s ru fixture → non-empty segments with
  increasing timestamps; unit test that the JSON shape matches mlx's.
- **Done when**: smoke green on Linux and Windows CI (CPU).

### 3.3 parakeet on Linux/Windows: onnx-asr

- **AS IS**: parakeet-mlx only.
- **TO BE**: `onnx-asr` backend:
  - model `nemo-parakeet-tdt-0.6b-v3` with VAD for long audio;
  - token timestamps grouped into sentences;
  - provider auto (CUDA, DirectML on Windows, CPU).
- **Test first**: smoke on the 25 s de fixture → text contains "Wetter", sentence count ≥ 2.
- **Done when**: green on Linux and Windows CI (CPU, slow job allowed).

### 3.4 GigaAM device selection

- **AS IS**: `mps` or `cpu`.
- **TO BE**: `cuda` → `mps` → `cpu`; venv Python 3.13 on every OS. Optional: onnx-asr GigaAM v3 e2e
  instead of PyTorch if the torch install is the Windows pain point (decide during the step).
- **Test first**: unit test for the device chooser with mocked `torch.cuda` / `torch.backends.mps`.
- **Done when**: green; one real run on Linux CPU.

### 3.5 OCR on Linux/Windows: RapidOCR

- **AS IS**: Apple Vision (Swift) only.
- **TO BE**: OCR interface with two backends:
  - Vision on macOS;
  - RapidOCR elsewhere, model chosen by the run's scripts (latin, cyrillic, cjk, arabic, devanagari).
  
  Same `ocr.jsonl`.
- **Test first**: fixtures = 3 rendered slides (ru, zh, ar) → OCR text contains the slide title, on
  every OS.
- **Done when**: green on 3 OSes.

### 3.6 Apple engine and Swift build: macOS only

- **AS IS**: `build_swift` runs whenever apple/OCR is needed; fails without `swiftc`.
- **TO BE**: Swift helpers are built only on macOS. On Linux/Windows the Apple engine is reported as
  "not available on this platform" in the log, not as a failure.
- **Test first**: on Linux, `-e apple` → exit 0, log says unavailable, no failed jobs.
- **Done when**: green on 3 OSes.

### 3.7 `vidx setup` per OS

- **AS IS**: Homebrew installs ffmpeg/deno/uv; `.venv/bin/python` paths.
- **TO BE**:
  - setup checks ffmpeg, deno and uv and prints the exact install command per OS (brew / apt / dnf /
    winget) instead of running a package manager itself;
  - creates engine environments with uv per OS (`Scripts\python.exe` on Windows);
  - pre-downloads models for the platform's backends;
  - detects CUDA and reports which device each engine will use.
- **Test first**: `vidx setup --dry-run` on each CI OS lists the expected backends and commands.
- **Done when**: green on 3 OSes; real `vidx setup` on one Linux and one Windows machine.

### 3.8 Fonts and paths

- **AS IS**: contact-sheet font from `/System/Library/...`, falls back to a tiny default.
- **TO BE**: bundled DejaVu Sans Bold (OFL) in the package.
- **Test first**: contact sheet label renders at 22 px on every OS (measure the text box).
- **Done when**: green on 3 OSes.

## Phase 4. Mixed speech with any number of languages (3–4 days)

### 4.1 Language identification on the audio, per segment

- **AS IS**: regions come from parakeet sentences labelled Cyrillic vs Latin. Only 2 scripts, no
  same-script pairs, no ja/zh/hi/ar; mix3 → one "uk" region.
- **TO BE**: `regions.py` v2:
  1. cut speech into 2–8 s segments at pauses (the GigaAM cutter, shared);
  2. whisper `detect_language` per segment (turbo; MLX on macOS, faster-whisper elsewhere);
  3. take the most probable language **among the `-l` list**;
  4. smooth: a segment shorter than 2 s or with low confidence takes its neighbours' label; minimum
     region 3 s;
  5. if parakeet ran and supports all listed languages, snap boundaries to its sentence edges.
- **Test first**:
  - unit: smoothing on synthetic label sequences (`uk uk en uk uk` → all uk; `uk uk en en en de de`
    → three regions);
  - fixture mix3 → regions uk/en/de with boundaries within 1 s of 6.4 s and 11.6 s;
  - fixture ru+en → same regions as today ±0.5 s.
- **Done when**: green on 3 OSes (smoke job, turbo model).

### 4.2 N languages through the whole pipeline

- **AS IS**: mixed mode assumes 2 languages (`other = first non-ru`), parakeet always runs first.
- **TO BE**:
  - `-l a,b,c,...` accepted (any count);
  - whisper/turbo/Vosk/Apple queued per language that has at least one region;
  - GigaAM on ru regions;
  - parakeet only if every region language is in its set (as built: every **listed** language, since
    parakeet runs before the regions exist and its sentences snap their boundaries);
  - merge uses each region's language row from the table (thresholds, joiner, fillers).
- **Test first**: fake engines with `-l uk,en,de` → 3 whisper jobs with the right clip timestamps; merge
  of a 3-region fixture picks the right source per region.
- **Done when**: green.

### 4.3 Same-script pairs

- **AS IS**: ru+uk or de+fr cannot be separated.
- **TO BE**: covered by 4.1 (audio LID, not script). Add a guard: if LID confidence between two listed
  languages is low across the whole file, log a warning ("ru/uk could not be separated reliably").
- **Test first**: fixture ru (Milena) + uk (Lesya) → 2 regions; fixture de + fr → 2 regions.
- **Done when**: green.

### 4.4 Auto-detection of 3+ languages

- **AS IS**: `-l auto` can return several languages but was tuned for 2.
- **TO BE**: same voting (≥ 2 clips and ≥ 20 %), no limit on the count; documented that auto finds
  only languages with long passages.
- **Test first**: synthetic vote counters → expected lists for 1, 2, 3 languages.
- **Done when**: green.

## Phase 5. Validation and release (2–3 days)

### 5.1 Real-hardware checklist

- **AS IS**: only macOS tested for real.
- **TO BE**: one documented run each on:
  - Linux + NVIDIA;
  - Windows + NVIDIA;
  - Windows CPU-only;
  - macOS (Apple Silicon).
  
  Each runs: ru meeting with screen share (`--meet`), the 9 language clips, mix3, a YouTube URL.
  Results are recorded in `docs/validation.md` (time per engine, errors).
- **Done when**: all four machines produce correct transcripts; timings go into the README table.

### 5.2 Belarusian with a real recording

- **AS IS**: no be test (no TTS voice); only whisper/turbo support it.
- **TO BE**: a short public-domain/CC recording (e.g. Mozilla Common Voice be) as a fixture.
  - Merge with whisper as base, turbo as fallback.
  - OCR with ru+uk.
  - README states the lower quality.
- **Test first**: smoke: be fixture → whisper output is Cyrillic with ў/і, merge uses whisper.
- **Done when**: green; README language table complete.

### 5.3 Docs and release

- **AS IS**: README/README_RUS describe macOS and ru/en.
- **TO BE**: both READMEs gain:
  - per-OS install sections;
  - an engine × platform table;
  - a language × engine table (the one above);
  - the N-language mixed mode;
  - known limits.
  
  Tag `v0.2.0`.
- **Done when**: CI green on 3 OSes, tag pushed (ask before pushing).

## Open questions (decide during the steps, not blocking the start)

1. Keep MLX engines on macOS or use onnx-asr/faster-whisper everywhere for one code path? Plan keeps
   MLX for speed; revisit after 3.2/3.3 timings.
2. Native Windows CUDA for faster-whisper needs cuBLAS/cuDNN DLLs; ship instructions or detect and fall
   back to CPU? Plan: detect + fall back + print instructions.
3. Vosk de model: 1.9 GB `de-0.21` vs 4.4 GB `de-tuda-0.6-900k` (better WER). Plan: 0.21, override via
   `VIDX_VOSK_<LANG>`.
