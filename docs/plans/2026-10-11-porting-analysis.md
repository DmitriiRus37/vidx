# vidx on Linux and Windows: analysis and step list

Status: written 2026-10-11 on branch `porting` (from `languages`, commit `5c4b82f`), before any port code;
sections 1-9 are the analysis as written then. It refines Phases 0.2, 2, 3 and 5 of
`2026-10-10-platforms-and-languages.md` against the code as it was. Section 10 records what was built,
where the build differs, and what was validated where.

Goal (unchanged): macOS, Linux and native Windows as equal platforms: same CLI, same output files, same
tests on all three; no WSL and no bash on Windows.

How the facts were collected:

- the code at `5c4b82f` (`vidx` bash script, `lib/`, `tests/`); `file:line` below refers to that commit;
- PyPI JSON (`https://pypi.org/pypi/<pkg>/json`) and upstream sources on GitHub, fetched 2026-10-11;
- probes in a Linux container on this Mac (Docker Desktop, `python:3.13-slim-trixie`, linux/aarch64,
  ffmpeg from Debian, uv from PyPI). Their outputs are quoted in section 3.

---

## 1. Inventory: everything platform-specific

Each row: what is there now, why it does not work on Linux and/or Windows, the chosen solution, the
alternatives and why they lost. Sources are listed in section 3.

### 1.1 Entry point and shell

| # | Where | Today | Problem | Solution | Alternatives considered |
|---|---|---|---|---|---|
| 1 | `vidx` (695 lines) | bash 3.2 script: options, job queue, setup | Windows has no bash | Python package `src/vidx/` with a `vidx` console script; the repo-root `vidx` becomes a short POSIX `sh` launcher for checkouts | Git Bash/MSYS2 on Windows (rejected: the goal says native, no bash); keeping bash on POSIX plus a separate Windows script (rejected: two implementations of one contract) |
| 2 | `vidx:331-359` | hand-written option loop: `val`, `num`, exact messages, the first argument is always the input | the contract tests pin the messages and exit status 1 | the same loop in Python, message for message | `argparse` (the old plan 2.2): rejected, it changes behaviour: `--out=x`, prefix abbreviations (`--ou`), its own error format and exit status 2, `-o -l` would be refused instead of taking `-l` as the value |
| 3 | `vidx:512, 523, 526` | `printf %q` + `eval` of queued command strings | bash only; `eval` of strings is fragile | each job is an argv list run without a shell (`subprocess`, `shell=False`) or a Python callable | — |
| 4 | `vidx:221, 350` | `awk` turns `[h:]m:s` into seconds, prints with awk's `%.6g`, compares with awk | no awk on Windows | Python with the same formatting (integers exact, others `%.6g`) and the same lenient number prefix rule, so `--cut` boundaries stay byte-identical | `float()` (rejected: `1:02:03.456` would format differently from today) |
| 5 | `vidx:521` | `tr -c 'A-Za-z0-9@.-' '_' \| tr -s _` for log names | no `tr` | `re.sub` with the same classes and the squeeze | — |
| 6 | `vidx:533, 691` | `tail -5 \| sed`, `paste -sd,` | no coreutils | Python | — |
| 7 | `vidx:443, 497, 555` | `python -I -c` snippets read `info.json` and `regions.json` | none, but they exist only because bash cannot parse JSON | read in-process with `json` | keeping the snippets (rejected: nothing left to isolate once the orchestrator is Python) |
| 8 | `vidx:568, 503` | `lang_field` / `languages.py --ocr` run in a subprocess | none | `languages.py` loaded in-process from its file (`importlib`, no `sys.path` change); the CLI of `languages.py` stays for scripts | — |
| 9 | `vidx:215-216` | colours if `[[ -t 2 ]]` | Windows consoles show raw escapes unless virtual-terminal processing is on | colours only if stderr is a TTY and `NO_COLOR` is unset; on Windows enable VT processing (`SetConsoleMode`) and drop colours if that fails | always plain (rejected: macOS output would change) |
| 10 | `vidx:6, 339, 462` | `readlink -f` (script dir, glossary, input) | GNU/BSD differences, none on Windows | `os.path.realpath` | — |
| 11 | `vidx:247, 296, 632` | `mktemp -d` | none on Windows | `tempfile.mkdtemp` / `TemporaryDirectory`; the Vosk temp dir stays inside `models/` (same volume, atomic rename) | — |
| 12 | `vidx:632-635` | frames go to a temp dir outside `-o`, because a `%` in the path breaks ffmpeg's `%06d.jpg` pattern | the temp path can contain `%` too (a Windows user name) | keep the temp dir and also escape `%` as `%%` in the pattern's directory part | — |
| 13 | `vidx:421` | `%` → `%%` in yt-dlp output templates | none | kept | — |

### 1.2 Processes and signals

| # | Where | Today | Problem | Solution | Alternatives considered |
|---|---|---|---|---|---|
| 14 | `vidx:232-234` | `trap on_interrupt INT TERM`; `kill_tree` walks children with `pgrep -P` | no `pgrep`, no POSIX signals on Windows | every job's process is started in its own group (POSIX `start_new_session=True`; Windows `CREATE_NEW_PROCESS_GROUP`) and registered; on SIGINT/SIGTERM (Windows also SIGBREAK) vidx collects each tree with `psutil.Process.children(recursive=True)`, terminates it, waits 3 s, kills what is left, exits 130. POSIX also sends `killpg`, so the tree dies even without psutil; Windows without psutil falls back to `taskkill /PID n /T /F` | Windows Job Objects with KILL_ON_JOB_CLOSE via `ctypes` (strongest: children die even if vidx crashes) — rejected for now: Win32 handle code that cannot run anywhere but Windows, while the psutil path runs on POSIX in the tests too; recorded as a hardening option. `taskkill` only (rejected as primary: untestable off Windows) |
| 15 | `vidx:541` | `jobs -rp` polled every second for `-j N` | bash only | `ThreadPoolExecutor(max_workers=N)`; jobs are submitted in queue order, so they start in that order; the main thread waits with a 0.5 s timeout so Ctrl-C is delivered on Windows too (a lock wait without timeout is not interruptible there) | `asyncio` subprocesses (rejected: frames+OCR is a multi-step Python job, threads are simpler) |
| 16 | `vidx:523-526` | foreground jobs inherit stdin | an engine in its own session must not read the terminal | stdin is `/dev/null` (`DEVNULL`) for every job | — |
| 17 | `tests/test_cli.py:77-103` | Ctrl-C test: SIGINT to the process group | POSIX only | kept for POSIX; a Windows variant sends `CTRL_BREAK_EVENT` to vidx started with `CREATE_NEW_PROCESS_GROUP` (skipped elsewhere); the Windows branches of the kill code are unit-tested with a simulated platform | — |

### 1.3 Files, paths, text

| # | Where | Today | Problem | Solution | Alternatives considered |
|---|---|---|---|---|---|
| 18 | `vidx:465` | `ln -s` the local input to `source.<ext>` | symlinks need admin rights or Developer Mode on Windows | try the symlink; if the OS refuses, log it and read the input from its own path (recorded in `.vidx/input`, as today) — `source.*` is then absent | hard link (rejected: a third behaviour, fails across volumes); copying (rejected: doubles disk use for long videos) |
| 19 | `vidx:8-9`, `vidx:223` | `.venv/bin/python`, `$(uv tool dir)/mlx-whisper/bin/python` | Windows venvs use `Scripts\python.exe` | one helper `venv_python(dir)` (`Scripts/python.exe` on Windows) | — |
| 20 | `vidx:6-10` | venvs, `models/`, compiled helpers live in the checkout | an installed package (`uv tool install`) has no checkout | `home`: `VIDX_HOME` if set; else the checkout when the package runs from one (so the existing `.venv`, `.venv-giga`, `models/` keep working); else the per-user data dir (`~/Library/Application Support/vidx`, `$XDG_DATA_HOME/vidx` or `~/.local/share/vidx`, `%LOCALAPPDATA%\vidx`) | `platformdirs` (rejected: a dependency for three lines) |
| 21 | `lib/*.py` (15 `open()` calls, list in `git grep -n "open(" lib`) | `open()` without `encoding` | Windows uses the ANSI code page (cp1252): writing Cyrillic raises `UnicodeEncodeError` | every text `open()` gets `encoding="utf-8"`; every text write also `newline="\n"` (else Windows writes CRLF and the outputs differ from macOS); a test parses `lib/` and `src/` and refuses an `open()` without them; child Pythons also get `PYTHONUTF8=1` (third-party code, stdout in pipes) | `PYTHONUTF8=1` alone (rejected: the scripts can be run by hand, and it does not stop CRLF); a test in a Latin-1 locale (kept as an extra where the locale exists, macOS) |
| 22 | stdout of helpers redirected to files (`lang.txt`, `ocr.jsonl`) | a Python child on Windows writes `\r\n` to a redirected stdout | output bytes differ by OS | helpers that print data reconfigure `sys.stdout` with `newline="\n"` (`common.unix_stdout()`) | normalising in the orchestrator (rejected: rewrites bytes it did not write) |
| 23 | orchestrator log lines contain `→ ≤ …` | stderr redirected to a file on Windows encodes with cp1252: `UnicodeEncodeError` | the orchestrator reconfigures stdout/stderr to UTF-8 (errors `backslashreplace`) on Windows | ASCII-only messages (rejected: the tests pin several messages with `→`) |
| 24 | `tests/*`: `endswith("/models/" + model)` etc. | `/` in expected paths | backslashes on Windows | compare `Path(...).parts` | — |
| 25 | `lib/frames.py:79` | label font `/System/Library/Fonts/Supplemental/Arial Bold.ttf`, else Pillow's 10 px bitmap font | the fallback is unreadable on contact sheets | macOS keeps Arial Bold (identical sheets); elsewhere `ImageFont.load_default(size=22)`, Pillow's bundled scalable Aileron (Pillow ≥ 10.1, with FreeType) | bundling DejaVu Sans Bold (the old plan 3.8; rejected: a 700 KB file + licence for a timestamp label, Pillow already ships a scalable font) |
| 26 | `lib/merge.py:308` | `ocr.md` header says "Apple Vision OCR" | wrong on Linux/Windows | merge takes `--ocr-name` (default "Apple Vision", so macOS output is unchanged) | a neutral header everywhere (rejected: changes macOS output) |

### 1.4 Tools and installation

| # | Where | Today | Problem | Solution | Alternatives considered |
|---|---|---|---|---|---|
| 27 | `vidx:283-286` | `setup` installs ffmpeg, deno, uv with Homebrew | no brew on Linux/Windows; running package managers needs sudo/elevation | `setup` checks for ffmpeg, ffprobe, deno, uv and prints the exact install command for the OS (brew; apt / dnf; winget) and stops if any is missing; it never runs a package manager | running apt/winget (rejected: sudo/UAC prompts, distro differences; the owner asked for printed commands) |
| 28 | `vidx:248-249` | Vosk models: `curl` + `unzip` | not on every Windows | `urllib.request` + `zipfile`, every member checked to stay inside the temp dir (zip-slip), moved into place only when complete | — |
| 29 | `vidx:19` | GigaAM installed from `git+https://…@<commit>` | needs `git`, not present on a fresh Windows | the GitHub archive of the same commit (`…/archive/<commit>.zip`), no git needed (probe 3.6) | — |
| 30 | `vidx:236-240, 292-293` | Swift helpers compiled into `lib/` | macOS only; an installed package should not write into its own directory | built only on macOS, into `<home>/bin/` | — |
| 31 | `vidx:32, 114-116` (help) | "Everything runs on this Mac", engine table with MLX/ANE | wrong elsewhere | help texts generated per platform from the engine registry | — |

### 1.5 Engines

| # | Engine | macOS (kept) | Linux / Windows | Why this backend |
|---|---|---|---|---|
| 32 | whisper, turbo | `mlx_whisper` CLI (`vidx:257-263`) | `lib/whisper_fw.py`: faster-whisper 1.2.1 (CTranslate2) with the **same command line** as `mlx_whisper` for the options vidx uses, and the same JSON (`text`, `segments[id, seek, start, end, text, tokens, temperature, avg_logprob, compression_ratio, no_speech_prob]`, `language`); CUDA float16 if `ctranslate2.get_cuda_device_count() > 0`, else CPU int8; models `large-v3`, `large-v3-turbo` | the old plan's choice, confirmed: `clip_timestamps`, `initial_prompt`, `condition_on_previous_text`, `language` exist with mlx semantics (3.1); wheels for win_amd64 and Linux x86_64/aarch64 for Python 3.9–3.14. Alternatives: `openai-whisper` (PyTorch; slower on CPU, heavier install), whisper.cpp (no Python wheel with the options above) |
| 33 | language ID (`lib/lid.py`) | mlx-whisper turbo, first decoder token | `lid.load()` picks the backend that its interpreter has: mlx-whisper, else faster-whisper `detect_language(clip, language_detection_segments=1)` → `all_language_probs` (3.1) | the swap point the language work prepared (`lib/lid.py:12-17`) |
| 34 | parakeet | `parakeet-mlx` CLI (`vidx:264-266`) | `lib/parakeet_onnx.py`: onnx-asr 0.12.0 `nemo-parakeet-tdt-0.6b-v3` with Silero VAD and token timestamps, cut into sentences at `.?!`; same CLI and same JSON as `parakeet-mlx` (`text`, `sentences[text, start, end, duration, confidence, tokens[…]]`); int8 on CPU, full precision on CUDA/DirectML | probe 3.2: Russian sentences with punctuation in 4.3 s for 43 s of audio on CPU. Alternatives: NeMo (PyTorch + NeMo toolkit, very heavy, Linux-first), sherpa-onnx (good, but its own model packaging and no ready parakeet v3 sentence output) |
| 35 | GigaAM | PyTorch, `mps` or `cpu` (`lib/gigaam_run.py:27`) | the same script and model; device `cuda` → `mps` → `cpu`; torch from PyTorch's CPU or CUDA index | keeps one GigaAM implementation and identical decoding on every OS (probe 3.6 installs it on Linux CPU). Alternative verified and kept in reserve: onnx-asr `gigaam-v3-e2e-rnnt` (probe 3.2: punctuated output, no torch), not chosen because its decoding is a re-implementation, so transcripts could differ from macOS |
| 36 | apple | Swift `apple_asr` (`vidx:269-279`) | not available: logged "not available on this platform", skipped, not a failure | Apple API |
| 37 | vosk | `lib/vosk_run.py` in the helper venv | the same: `vosk==0.3.44` has wheels for macOS universal2, win_amd64, Linux x86_64/aarch64 (`py3-none`, any Python 3) | already portable. 0.3.45 dropped the macOS wheel, so the pin stays |
| 38 | OCR | Swift `ocr` + Vision (`lib/ocr.swift`) | `lib/ocr_rapid.py`: RapidOCR 3.10 (`rapidocr[rtl]` + onnxruntime), detector PP-OCRv6 small (default), recogniser PP-OCRv5 mobile per script; the **same command line** (`<groups> <image>…`, Vision locale groups from `languages.ocr_arg`) and the same merge (first group's lines, later groups add lines whose boxes overlap < 50 %, sorted top to bottom); models under `<home>/models/rapidocr` | probe 3.3: reads ru/uk (eslav), de (latin), zh/ja (ch), ar (arabic) titles exactly; one model per script, like Vision. Gap: **Hindi is not detected** by any RapidOCR detector tried (3.3). Alternatives: Tesseract (system install per OS, weaker on slides), EasyOCR (PyTorch), PaddleOCR (paddle wheels, no Windows GPU) |
| 39 | yt-dlp | uv tool `yt-dlp[default]` + deno | the same on every OS (uv tool, deno from winget/official installer) | `[default]` brings `yt-dlp-ejs`, deno is the default JS runtime (3.7) |

---

## 2. Target architecture

### 2.1 Layout

```
vidx                    POSIX sh launcher for a checkout (macOS/Linux): runs `python -m vidx` from src/
pyproject.toml          [project] vidx, console script vidx = vidx.cli:main; deps: psutil
src/vidx/
  __main__.py           python -m vidx
  cli.py                commands (help, find, setup, run), the option loop, exit codes
  texts.py              help texts (per platform, from the registry)
  paths.py              home dir, venv python, platform name (one place that reads sys.platform)
  state.py              output dir marker, stamps, input tracking, failed list
  jobs.py               job queue (-j), logs, process groups, kill trees, Ctrl-C
  engines.py            the registry: per platform, the program that runs each engine
  pipeline.py           source, audio, language, regions, queue building, merge
  setup.py              vidx setup: checks, install commands, uv environments, models, devices
  lib/                  the scripts the engines' interpreters run (was lib/): merge, frames, regions,
                        detect_lang, lid, gigaam_run, vosk_run, find, languages, common,
                        whisper_fw, parakeet_onnx, ocr_rapid, *.swift
tests/fakes/hook.py     VIDX_FAKE_ENGINES=1: swaps each engine's program for tests/fakes/engine.py
```

`lib/` moves into the package so that an installed copy has it; the scripts stay standalone (they run in
other interpreters: mlx-whisper's, `.venv-giga`, the engine venvs) and keep importing their siblings
as top-level modules. The tests put `src/vidx/lib` on `sys.path` as they put `lib` there today.

### 2.2 Engine contract: one command line per engine, any backend

The pipeline builds the same arguments on every OS; only the program differs:

| engine | arguments (unchanged from today) | macOS program | Linux/Windows program |
|---|---|---|---|
| whisper, turbo | `<audio> --model M -o DIR --output-name STEM -f json --verbose False --condition-on-previous-text False --language L [--initial-prompt P] [--clip-timestamps C]` | `mlx_whisper` | `<.venv-whisper python> lib/whisper_fw.py` |
| parakeet | `<audio> --model M --output-format json --output-dir DIR --output-template parakeet` | `parakeet-mlx` | `<.venv-onnx python> lib/parakeet_onnx.py` |
| gigaam | `<audio> <out.json> [regions.json]` | `<.venv-giga python> lib/gigaam_run.py` | same |
| vosk | `<model dir> <audio> <out.json>` | `<.venv python> lib/vosk_run.py` | same |
| apple | `--timeout S <locale> <audio> <out.json>` / `--locales` | `<home>/bin/apple_asr` | — (not available) |
| ocr | `<groups> <image>…` | `<home>/bin/ocr` | `<.venv-whisper python> lib/ocr_rapid.py` |
| language ID | `lib/{detect_lang,regions,lid}.py <model> …` | mlx-whisper's python, model `mlx-community/whisper-large-v3-turbo` | `<.venv-whisper python>`, model `large-v3-turbo` |

`engines.py` holds, per platform, the program, the model id and the label of each engine. The fake hook
replaces each program with `python -I tests/fakes/engine.py <engine>`; because the arguments are the same
everywhere, the fake engine and every CLI test are the same on all three platforms. Output stems
(`whisper-large-v3`, `parakeet`, …) and JSON shapes are the same on every platform, so `merge.py` and
`find.py` do not know which backend ran; shape tests pin this.

### 2.3 Environments per platform

| env (under `<home>`) | macOS | Linux / Windows | contents |
|---|---|---|---|
| `.venv` (helper; Python 3.14) | ✓ | ✓ | vosk, pillow, numpy, psutil: frames, merge, find, Vosk |
| `.venv-giga` (3.13) | ✓ | ✓ | GigaAM + torch (PyTorch index: CPU, or CUDA cu126 when an NVIDIA GPU is found) |
| uv tools | mlx-whisper, parakeet-mlx, yt-dlp | yt-dlp | |
| `.venv-whisper` (3.13) | — | ✓ | faster-whisper, rapidocr[rtl], onnxruntime (CPU; faster-whisper requires the CPU package for its VAD) |
| `.venv-onnx` (3.13) | — | ✓ | onnx-asr + one onnxruntime flavour: `onnxruntime-gpu<1.27` (CUDA 12, the same CUDA as CTranslate2), `onnxruntime-directml` (Windows, as built only with `--gpu directml`), else `onnxruntime` |

Why two engine venvs on Linux/Windows: `onnxruntime`, `onnxruntime-gpu` and `onnxruntime-directml` install
the same `onnxruntime` import package, and faster-whisper depends on the CPU one; keeping the GPU
flavour in its own venv avoids the overwrite. 3.13 because `onnxruntime-directml` and the GigaAM pins
were checked there; overridable like today (`VIDX_PYTHON_ENGINES`).

The orchestrator itself needs only the standard library and psutil; on POSIX psutil is optional
(`killpg` covers the tree). So the checkout launcher runs with `.venv/bin/python` when it exists (the
owner's current setup keeps working), else with any Python ≥ 3.11 that uv finds.

### 2.4 How each existing test maps

| tests | today | after |
|---|---|---|
| `test_cli.py`, `test_cli_contract.py`, `test_belarusian.py` (CLI parts) | run `/bin/bash vidx` with `tests/fakes/hook.sh` | run `python -m vidx` (the code in `src/`) with `tests/fakes/hook.py`; path assertions OS-neutral; the POSIX Ctrl-C test kept, a Windows Ctrl-Break twin added |
| `test_merge_*`, `test_fillers`, `test_markdown`, `test_regions`, `test_detect_lang`, `test_languages`, `test_frames`, `test_smoke` | import `lib/` modules | same, `lib` path changed |
| `test_real_*` (28, macOS) | build Swift into a temp dir, mlx language ID | unchanged (`lid_backend` reads the model id from `engines.py` instead of the bash script) |
| new | — | `test_options`, `test_state`, `test_jobs` (queue, trees, Ctrl-C, Windows branches simulated), `test_engines` (registry per platform, JSON shapes), `test_text_io` (utf-8/LF rule), `test_setup` (dry run per OS), `test_whisper_fw`, `test_parakeet_onnx`, `test_ocr_rapid` (backends with stub libraries), `test_real_engines` (marker `real`, Linux/Windows engines with small models) |

---

## 3. Evidence

### 3.1 faster-whisper / CTranslate2

- faster-whisper 1.2.1 (2025-10-31), `py3-none-any`, `requires_python >=3.9`; depends on `ctranslate2>=4.0,<5`,
  `onnxruntime>=1.14,<2` (Silero VAD), `av>=11` (https://pypi.org/pypi/faster-whisper/json).
- `transcribe(audio, language=…, initial_prompt=…, condition_on_previous_text=…, clip_timestamps: Union[str,
  List[float]] = "0", …)`; clip timestamps are "Comma-separated list start,end,start,end,… (in seconds)",
  parsed with `float(ts) for ts in clip_timestamps.split(",")`
  (https://github.com/SYSTRAN/faster-whisper/blob/v1.2.1/faster_whisper/transcribe.py, L747-800, L1114-1125)
  — the same format vidx already passes to mlx_whisper.
- `WhisperModel.detect_language(audio: np.ndarray (16 kHz float), …, language_detection_segments=1)` →
  `(language, probability, all_language_probs)` (same file, L1768-1839).
- Model aliases: `large-v3` → `Systran/faster-whisper-large-v3`, `large-v3-turbo` →
  `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, `tiny` → `Systran/faster-whisper-tiny` (`faster_whisper/utils.py:13-30`).
- CTranslate2 4.8.2: wheels cp39–cp314 for win_amd64, manylinux x86_64/aarch64, macOS
  (https://pypi.org/pypi/ctranslate2/json). `ctranslate2.get_cuda_device_count()` returns 0 without CUDA.
  GPU: CUDA 12 + cuBLAS; cuDNN optional since CTranslate2 4.6.3 ("makes cuDNN an optional dependency",
  CHANGELOG). On Windows the CUDA DLLs are not in the wheel: the faster-whisper README points to installing
  CUDA 12 / cuBLAS or Purfview's library archive on `PATH`.
- **Probe (container, CPU, `tiny`)**: transcribing a *file path* fails: `TypeError: open() got an unexpected
  keyword argument 'metadata_errors'` — faster-whisper 1.2.1's `decode_audio` vs PyAV 19.0.1. Passing a
  float32 numpy array (vidx already has 16 kHz mono WAV) works: Russian segments `(0.0, 17.36)`,
  `(30.4, 40.4)` for `clip_timestamps="0,17.8,30.4,40"` on `ru_en.m4a`; `detect_language` on 20-28 s →
  `en 0.984`, 99 languages. Decision: `whisper_fw.py` reads the WAV itself (no PyAV decoding) and av is
  pinned anyway.

### 3.2 onnx-asr

- onnx-asr 0.12.0 (2026-07-15), Python 3.10–3.14, extras `[cpu]` (onnxruntime), `[gpu]` (onnxruntime-gpu),
  `[hub]`; DirectML via `onnxruntime-directml` installed by hand (https://pypi.org/pypi/onnx-asr/json,
  `docs/installation.md`). "`onnxruntime-gpu` 1.27 and newer require CUDA 13. Earlier versions require
  CUDA 12." (same file).
- `load_model(name, quantization=None|"int8", providers=…)`; `load_vad("silero")`; `.with_vad(vad)`,
  `.with_timestamps()` → segments with `start, end, text, tokens, timestamps` (`src/onnx_asr/loader.py`,
  `vad.py`, `asr.py`). "The maximum audio length for most models is 20–30 seconds. For longer audio, use
  VAD" (README).
- **Probe (container, CPU, int8, `ru_en.m4a` 43 s)**: `nemo-parakeet-tdt-0.6b-v3` loaded in 43 s (first
  download), recognised in 4.3 s: `0.07-0.93 Добрый вечер.`, `1.03-3.49 Сегодня мы готовим овощной суп
  на ужин.` …; token timestamps are relative to the segment start. `gigaam-v3-e2e-rnnt`: same sentences
  with punctuation in 2.9 s.

### 3.3 RapidOCR

- `rapidocr` 3.10.0 (2026-10-08), `py3-none-any`; onnxruntime is not a declared dependency (install it
  explicitly); Arabic needs the `[rtl]` extra (probe: `ModuleNotFoundError: RTL recognition requires the
  optional dependency 'python-bidi'`). Models are downloaded on first use into the package directory
  unless `Global.model_root_dir` is set.
- Defaults: detector and recogniser PP-OCRv6 small, `lang_type` ch. PP-OCRv6 rec has no Cyrillic. PP-OCRv5
  mobile rec: ch, korean, latin, eslav, en, th, el, arabic, cyrillic, devanagari, ta, te (probe error text
  and `default_models.yaml`). `eslav` = Russian, Belarusian, Ukrainian, English (PaddleOCR PP-OCRv5
  multi-language docs).
- **Probe (slides rendered with Arial Unicode as in `tests/test_real_ocr.py`; det PP-OCRv6 small, rec
  PP-OCRv5 mobile)**: eslav → `Деплой во вторник`, `Їжак і ґанок`, and on the ru+zh slide `Погода на
  выходные`, `Деплой в Kubernetes` (Chinese line skipped); latin → `Größe und Übergabe`; ch →
  `週末の天気予報`, `周末的天气预报` and Cyrillic as Latin look-alikes (`NoroAa Ha BbixoAHble`); arabic →
  `طقس نهاية الأسبوع`; devanagari → nothing (also with PP-OCRv4/PP-OCRv5 server detectors only `बैठक`).
  So RapidOCR behaves like Vision (one script per model), the same group-and-merge works, and Hindi OCR is
  a known gap on Linux/Windows.

### 3.4 psutil and Windows process groups

- psutil 7.2.2: abi3 wheels incl. win_amd64/arm64, manylinux, macOS (https://pypi.org/pypi/psutil/json).
  `children(recursive=True)`, `wait_procs(procs, timeout)` → `(gone, alive)`; on Windows `terminate()` is
  TerminateProcess (psutil docs, "kill process tree" recipe).
- `subprocess.CREATE_NEW_PROCESS_GROUP`: "necessary for using os.kill on the subprocess"; `CTRL_C_EVENT`
  cannot be sent to a process group, `CTRL_BREAK_EVENT` can (Python `subprocess` docs; Microsoft
  `GenerateConsoleCtrlEvent` docs). A child in a new group has Ctrl-C disabled, so the console's Ctrl-C
  reaches only vidx, which kills the trees.

### 3.5 Vosk

- `vosk` 0.3.44: `py3-none` wheels for `macosx_10_6_universal2`, `win_amd64`, `win32`, manylinux x86_64 and
  aarch64; 0.3.45 has no macOS wheel (https://pypi.org/pypi/vosk/json). Pin kept at 0.3.44.

### 3.6 GigaAM and torch

- The GigaAM repo (`pyproject.toml` on main, 0.2.0): `onnxruntime==1.23.*`, extra `[torch]`;
  `load_model(..., device=None|str)` defaults to cuda if available (`gigaam/__init__.py:150-184`).
- torch 2.14.1: on Linux the PyPI wheel pulls CUDA 13 libraries; the Windows PyPI wheel is CPU-only;
  PyTorch's `cu126` index has 2.14.1 for win_amd64 and Linux (cu128 stops at 2.11.0)
  (https://download.pytorch.org/whl/cu126/torch/).
- **Probe (container)**: `torch==2.14.1 torchaudio==2.11.0` from `https://download.pytorch.org/whl/cpu`, then
  `gigaam[torch] @ https://github.com/salute-developers/GigaAM/archive/7447938….zip` (no git) → `torch
  2.14.1+cpu`, `gigaam 0.2.0`, `onnxruntime 1.23.2`, 816 MB venv. Installing torch from its own index first
  and the rest from PyPI avoids `--index-strategy unsafe-best-match` (dependency confusion).

### 3.7 yt-dlp, deno, ffmpeg, uv

- yt-dlp README: `yt-dlp-ejs` "Required for full YouTube support" (in `[default]`); "A JavaScript
  runtime/engine like deno (recommended) … is also required"; deno ≥ 2.3.0 (wiki EJS).
- deno: no official apt repository ("Deno does not publish an official apt repository"); install with
  `curl -fsSL https://deno.land/install.sh | sh`, or `winget install --id DenoLand.Deno -e`
  (https://docs.deno.com/runtime/getting_started/installation/).
- ffmpeg: Ubuntu 24.04 `ffmpeg`; Fedora ships `ffmpeg-free` (full ffmpeg from RPM Fusion); winget
  `Gyan.FFmpeg`. uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`, `winget install --id astral-sh.uv -e`.

### 3.8 GitHub Actions

- Labels: `ubuntu-24.04` (x64), `windows-2025`, `macos-14` (arm64) — but **macos-14 is being retired**:
  "fully unsupported by November 2nd, 2026", with brownouts in October 2026 (actions/runner-images issue
  #13518). The CI uses `macos-15` (arm64) instead.
- ffmpeg is not preinstalled on these images (runner-images software lists): the workflow installs it
  with the OS package manager (apt, brew, choco).
- `astral-sh/setup-uv` v10.3.0, pinned by commit SHA as its README shows (no floating `v10` tag).

---

## 4. Risks and how they are tested without Windows

| Risk | Mitigation / test |
|---|---|
| Windows process trees and Ctrl-C | psutil path unit-tested on POSIX for real; Windows creation flags, `taskkill` fallback and SIGBREAK handler unit-tested with a simulated platform; a Ctrl-Break end-to-end test that runs only on Windows (CI) |
| Encodings / CRLF on Windows | AST test over `src/` for `open()`; a merge run under a Latin-1 locale (macOS); outputs compared byte-for-byte with goldens |
| Paths with `Scripts\python.exe`, drive letters, `%` | `paths.venv_python` tested with simulated `os.name`; the frames pattern tested with a `%` in the temp dir |
| CUDA on Windows (cuBLAS DLLs for CTranslate2) | detection reports the device; on failure to load a GPU model the whisper backend falls back to CPU int8 and says so; manual checklist (section 9) |
| onnxruntime flavours overwriting each other | separate venvs (2.3); setup installs exactly one flavour |
| The fake platform differs from a real one | the CI matrix runs the same unit and CLI tests natively on Windows (`windows-2025`) — after the owner pushes a branch |
| Behaviour drift on macOS | the e2e suite (same assertions as the bash version), goldens, and a real-model comparison of `languages` (bash) vs `porting` (Python) on `mix3.m4a`, `ru_en.m4a`, `ru_slides.mp4` |
| Hindi OCR on Linux/Windows | documented gap; the OCR backend test lists hi as expected-to-miss |

---

## 5. Steps (ordered)

Each step: AS IS → TO BE, test first, done when. One step = one commit.

### P1. Package skeleton (old 2.1)
- AS IS: `vidx` bash; `lib/` loose scripts; `pyproject.toml` tool config only.
- TO BE: `src/vidx/` package, `lib/` moved to `src/vidx/lib/`, `[project]` with console script `vidx`,
  `python -m vidx help` prints the usage (texts moved to `texts.py`); the bash script uses the new lib path
  until P6.
- Test first: `python -m vidx --help` and `help run` print the bash texts; the console script entry exists.
- Done when: all 314 tests green with the new paths.

### P2. Text files: UTF-8 and LF (new; inventory 21-23)
- TO BE: every `open()` in `src/` passes `encoding="utf-8"`, writes `newline="\n"`; data-printing helpers
  reconfigure stdout.
- Test first: AST rule test fails on today's 15 calls; merge under a Latin-1 locale fails today.
- Done when: green; goldens unchanged.

### P3. Options and validation (old 2.2)
- TO BE: `cli.parse_run(args)` with the bash loop's semantics and messages.
- Test first: every contract row that exits before the pipeline, in-process.
- Done when: green.

### P4. Output dir, stamps, input, paths (old 2.3 + inventory 18-20)
- TO BE: `state.py` (marker, `fresh`, `stamp`, `same_input`, `find_source`), `paths.py` (home, venv
  python, platform), byte-identical stamps.
- Test first: option change → rerun, same → cached, foreign folder refused, other input → redo, stamp
  strings equal the bash ones, Windows venv path, home resolution.
- Done when: green.

### P5. Job queue, process trees, Ctrl-C (old 2.6 + 2.7)
- TO BE: `jobs.py`.
- Test first: `-j 2` never runs 3, order kept, a failure does not stop the rest, logs named like bash,
  kill tree kills grandchildren, simulated Windows flags/fallbacks.
- Done when: green on macOS and in the Linux container.

### P6. Engine registry + pipeline in Python; bash retired (old 2.4, 2.5, 2.8, 3.1, 3.6)
- TO BE: `engines.py` (darwin = today's commands; linux/win32 = new backends; apple unavailable),
  `pipeline.py`, `tests/fakes/hook.py`; the root `vidx` becomes the launcher; all CLI tests run the Python
  CLI.
- Test first: the whole existing CLI/contract suite against `python -m vidx`; registry tests (linux lists
  faster-whisper, darwin mlx, apple only on darwin); `-e apple` on a simulated Linux → exit 0, "not
  available on this platform".
- Done when: all tests green on macOS and Linux; the bash-written output dir is reported cached by the
  Python vidx (stamp compatibility); real macOS runs identical to `languages` (section 10).

### P7. Contact-sheet font (old 3.8, changed: no bundled font)
- Test first: with Arial Bold missing, the label text is ≥ 18 px tall.

### P8. whisper/turbo + language ID on Linux/Windows (old 3.2)
- TO BE: `lib/whisper_fw.py`, `lib/lid.py` faster-whisper branch.
- Test first: with a stub `faster_whisper` module: argv parsed like mlx_whisper, clip timestamps and prompt
  passed, JSON keys equal the mlx JSON keys; lid returns `{code: prob}`; real (marker `real`): `tiny` on a
  fixture → non-empty, increasing segments.

### P9. parakeet on Linux/Windows (old 3.3)
- TO BE: `lib/parakeet_onnx.py`, provider choice CUDA → DirectML → CPU.
- Test first: stub onnx_asr: sentences split at `.?!` with absolute times, parakeet-mlx keys; real: the
  Russian fixture → "Добрый вечер."

### P10. GigaAM device (old 3.4)
- Test first: chooser with stub torch: cuda → mps → cpu.

### P11. OCR on Linux/Windows (old 3.5)
- TO BE: `lib/ocr_rapid.py`, Vision groups → RapidOCR models, overlap merge.
- Test first: group mapping, merge order with fake boxes; real: the slides of `test_real_ocr.py` (hi expected
  to miss).

### P12. `vidx setup` per OS (old 3.7)
- TO BE: checks + printed install commands, uv environments per platform, model warm-up, device report,
  `--dry-run`.
- Test first: dry run for darwin/linux/win32 (simulated) lists the expected envs, commands and
  `Scripts\python.exe`.

### P13. CI (old 0.2)
- TO BE: `.github/workflows/ci.yml`: matrix macos-15/ubuntu-24.04/windows-2025 × Python 3.11/3.14, ruff +
  unit tests; `smoke.yml` (schedule + manual): Linux and Windows engine venvs with small models,
  `pytest -m real`.
- Test first: actionlint clean.
- Done when: lint clean locally; it has never run (no push from this session).

### P14. Validation and docs (old 5.1, 5.3)
- Linux container: unit tests + real engines on the fixtures; macOS: unit + real + comparison with
  `languages`; Windows: manual checklist (section 9). READMEs (both), CLAUDE.md.

---

## 6. Deviations from the 2026-10-10 plan

| Plan | Now | Why |
|---|---|---|
| 0.2 runners `macos-14` | `macos-15` | macos-14 is retired in Nov 2026, brownouts in Oct 2026 (3.8) |
| 2.1 install with `uv tool install`; venvs implicit | home dir rule (checkout, `VIDX_HOME`, data dir) | an installed package has nowhere else to keep venvs and models; the checkout keeps the owner's existing layout |
| 2.2 `argparse` | the same hand-written loop | argparse changes accepted syntax, messages and exit status (inventory 2) |
| 2.3 "no symlink; read from `.vidx/input`" | symlink where allowed, fallback to the input path | keeps macOS/Linux output (`source.*`) identical; Windows without symlink rights still works |
| 2.6 queue in Python | as planned | — |
| 2.8 bash `vidx` becomes a 3-line wrapper, removed later | the root `vidx` is a POSIX `sh` launcher kept for checkouts | it is the documented way to run from a checkout on macOS/Linux; Windows uses the console script |
| 3.1 `Engine` protocol with `languages(platform)`, `available()`, `command()` | registry of programs per platform; language support stays in `languages.py` | the language rules (parakeet set, Apple locales, Vosk models, GigaAM = ru) already live in one table; backends differ only in the program, so the protocol shrinks to "program + model + label" |
| 3.2 smoke "on the 5 s ru fixture" | the fixtures that exist: `ru_en.m4a`, `ru_slides.mp4`, `mix3.m4a` | no 5 s ru clip; `ru_slides.mp4` is 4 s |
| 3.3 test "de fixture contains Wetter" | Russian/English fixture | no de speech fixture besides `de_fr.m4a` (5 s de); the Russian sentence check is stronger for VAD + sentences |
| 3.4 "optional onnx-asr GigaAM" | decided: PyTorch everywhere; onnx-asr verified as fallback | same decoding on every OS (3.6) |
| 3.5 "RapidOCR model chosen by the run's scripts" | the same Vision groups mapped to RapidOCR models, one pass per group | one OCR command line for all platforms; mirrors the Vision fix of Phase 1.8 |
| 3.7 setup on "each CI OS" | dry-run tested per simulated OS; real setup run in the Linux container | no Windows machine |
| 3.8 bundled DejaVu font | Pillow's bundled scalable font | no extra file (inventory 25) |
| 5.1 four real machines | macOS + Linux container (CPU, aarch64) here; Windows and NVIDIA only by checklist | hardware available to this session |
| — | new: P2 (UTF-8/LF), ocr.md header, `%` in frame paths, PyAV workaround, two engine venvs | found by this analysis |

---

## 7. Open points (not blocking)

1. Windows Job Objects as a stronger guarantee that engines die with vidx (crash, closed console).
2. `onnxruntime-gpu` ≥ 1.27 needs CUDA 13 while CTranslate2 wheels are built for CUDA 12; revisit when
   CTranslate2 ships CUDA 13 builds.
3. Hindi OCR on Linux/Windows (RapidOCR's detectors miss Devanagari titles).
4. Thresholds were calibrated on MLX output; faster-whisper/onnx-asr output is the same model family but
   decoding differs — recalibrate on real recordings (old Phase 5).

---

## 8. Security properties kept

| Property | Python version |
|---|---|
| `python -I` for `-c` snippets | the snippets are gone (in-process JSON); lib scripts still run by path (their own directory first on `sys.path`, never the cwd); the fake engine with `-I` |
| `.vidx/` marker before writing into `-o` | `state.prepare_out` (same checks, same messages) |
| per-step option stamps | `state.fresh/stamp`, byte-identical to bash |
| `--ignore-config --no-playlist` for yt-dlp | unchanged argv |
| video id sanitising | `re.sub(r"[^A-Za-z0-9_-]", "_", id)` as bash's `${id//[^A-Za-z0-9_-]/_}` |
| escaped Markdown | `merge.md()` unchanged |
| Ctrl-C kills child trees | `jobs.py`, POSIX and Windows |
| no shell with untrusted input | every subprocess is an argv list, `shell=False`; no `eval` |
| downloads | Vosk zip members checked against zip-slip; GigaAM pinned by commit archive; torch only from PyTorch's index for torch itself |

---

## 9. Manual Windows validation checklist

Machine: Windows 11 x64, PowerShell. Record results (time per engine, errors) in a table.

1. `winget install --id astral-sh.uv -e`, `winget install --id Gyan.FFmpeg -e`,
   `winget install --id DenoLand.Deno -e`; new terminal; `uv --version`, `ffmpeg -version`, `deno --version`.
2. `uv tool install git+https://github.com/DmitriiRus37/vidx@porting` (or `uv tool install -e .` in a clone);
   `vidx help` prints the usage; `vidx setup --dry-run` lists `.venv\Scripts\python.exe` and the engine venvs.
3. `vidx setup`: finishes; reports devices (CPU; on NVIDIA: CUDA for whisper/parakeet/GigaAM).
4. Unit tests in a clone: `uv run --isolated --group test pytest` → all pass (no skips except the POSIX-only Ctrl-C
   test and `real`).
5. `vidx tests\fixtures\media\ru_slides.mp4 -l ru -o out` → exit 0; `out\transcript.md` is UTF-8 with LF line
   endings (`Format-Hex out\transcript.txt | Select -First 3` shows `0A`, no `0D`); `out\frames\sheets\sheet-01.jpg`
   has readable timestamps; `out\ocr.md` contains the slide text.
6. Same with `-j 3`: `out\logs\*.log` exist; Ctrl-C during the run → exit 130 within 5 s, Task Manager shows
   no `python.exe` left from vidx.
7. `-l ru,en` on `ru_en.m4a` → `regions.json` ru/en/ru, transcript in both languages.
8. `-e apple` → exit 0, log "Apple SpeechTranscriber: not available on this platform".
9. A path with spaces and Cyrillic (`C:\Users\…\Видео\мой файл.mp4`) and an `-o` with `%` → works.
10. Without Developer Mode: the log says the input is read in place (no symlink); with it: `out\source.mp4`
    is a symlink.
11. NVIDIA: whisper on CUDA (log says device cuda); remove cuBLAS from `PATH` → falls back to CPU with a message.
12. A YouTube URL with `-l en` → `source.mp4`, `subs\*.json3`, `info.json`.

---

## 10. As built and validated

### 10.1 Commits → steps

| Step | Commit(s) |
|---|---|
| analysis | `docs: porting analysis for Linux and Windows` |
| P1 package | `package: src/vidx with a vidx console script; lib/ moves into it` |
| P2 UTF-8/LF | `text: UTF-8 and LF for every text file vidx reads or writes` |
| P3 options | `options: the run option loop in Python, message for message` |
| P4 state, paths | `state: output folder, stamps and paths in Python` |
| P5 queue, Ctrl-C | `jobs: job queue, process trees and Ctrl-C in Python`; `tests: Ctrl-Break twin of the Ctrl-C test for Windows` |
| P6 registry + pipeline | `pipeline: the run, find and setup in Python; vidx becomes a launcher` |
| P7 font | `frames: readable contact-sheet labels without macOS fonts` |
| P8 whisper | `whisper: faster-whisper backend and language ID for Linux and Windows` |
| P9 parakeet | `parakeet: onnx-asr backend for Linux and Windows` |
| P10 GigaAM | `gigaam: device CUDA, then MPS, then CPU` |
| P11 OCR | `ocr: RapidOCR backend for Linux and Windows` |
| P12 setup | `setup: per-OS checks, environments, models and device report`; `setup: rebuild a venv whose Python is gone`; `setup: name the Apple GPU on macOS` |
| (new) | `engines: model overrides for small machines and smoke runs`; `texts: help per platform` |
| P13 CI | `tests: OS-neutral paths and UTF-8 decoding for Windows`; `ci: GitHub Actions on macOS, Linux and Windows` |
| P14 validation, docs | `tests: the real GigaAM test runs on macOS too`; `tests: the Apple timeout help is checked in the macOS help`; this section; READMEs and CLAUDE.md |
| first Windows CI (10.3) | `tests: UTF-8 and LF in the tests' own text files`; `log: LF on vidx's redirected stdout and stderr on Windows`; `tests: the stdout-to-file job prints LF itself`; `tests: compare the regions' parakeet path by its parts`; `tests: no file appended to by processes running together`; `state: one failed-job append at a time`; `jobs: wait for programs in slices, so Ctrl-Break stops -j 1 on Windows`; `lib: scripts print their data as UTF-8 whoever runs them`; `ci: run smoke on pushes to porting for now` (and its removal) |
| locale, find, program output (10.3) | `log: UTF-8 and LF on redirected output on every OS, no crash on a non-UTF-8 terminal`; `find: hits as UTF-8 with LF on every OS, printed through vidx's stdout`; `setup: name the encoding of every program output read as text`; `ci: the whole suite in a Latin-1 locale on Linux`; `jobs: arguments a non-UTF-8 locale cannot encode go to children as UTF-8`; `tests: the emulated terminals write LF on Windows too`; `tests: only ASCII on the command lines the tests start themselves`; `ci: run smoke on pushes to porting again for now` (and its removal) |
| open items of 10.4 (10.3) | `setup: a device probe that times out is reported, not fatal`; `setup: device probes and the GigaAM warm-up as lib scripts run by path`; `jobs: text arguments go to children as UTF-8 in every locale`; `lib: scripts' stderr is UTF-8 with LF when it goes to a file or pipe`; `jobs: engine stderr re-encoded for a non-UTF-8 terminal`; `tests: the relay tests' child writes LF on Windows too`; `ci: smoke fails when vidx setup cannot name a device`; `ci: run smoke on pushes to porting again for now` (and its removal) |

### 10.2 Where the build differs from sections 1-9

- **RapidOCR merge** (P11): unlike Vision, a RapidOCR recogniser reads other scripts as noise (the eslav
  model read the Arabic line of the ru+ar slide as `El lb`), so "first pass wins" lost that line. An
  overlapping line now goes to the pass whose text is more in its own model's script (`own_script`), a
  tie keeps the earlier pass. The `en` model is used only alone: every other PP-OCRv5 recogniser reads
  Latin letters too.
- **DirectML** (P12): opt-in (`--gpu directml`), not the default for Windows without NVIDIA: a VM or a
  machine without a DirectX 12 GPU would fail parakeet; the CPU always works.
- **Model overrides** (new): `VIDX_MODEL_WHISPER`, `VIDX_MODEL_TURBO` (language ID follows it),
  `VIDX_MODEL_PARAKEET`, `VIDX_VOSK_<LANG>` (the old plan's open question 3); used by the smoke job
  (tiny whisper, small Vosk models) and useful on CPU-only machines.
- **Help per platform** (new): the macOS help is byte-for-byte unchanged; Linux/Windows name their
  backends.
- **Ctrl-C with -j > 1** (P6): leaving a `ThreadPoolExecutor` block waits for its workers, which were
  blocked on the programs about to be killed; the pool is now shut down without waiting, the trees are
  killed and vidx exits with `os._exit(130)`.
- **Broken venvs** (P12, found in the container): a venv whose uv-managed Python was removed is rebuilt
  with `--clear` instead of failing.
- **Process-group kill** (P5): psutil reaps the killed parent, so `Popen.wait()` may report 0 afterwards;
  only liveness is checked.
- **TDD**: each step started with failing tests, except `log.py` (P4) and the pipeline itself (P6), whose
  acceptance tests were the existing end-to-end suite (green against bash, red until the port, green
  after) and whose new unit tests were written alongside.

### 10.3 Validation

**macOS** (this Mac, Apple Silicon, Python 3.14 and 3.11):

- unit + CLI: `499 passed, 2 skipped` (the 2 Windows-only Ctrl-Break tests) on 3.14 and 3.11;
- real (`HF_HUB_OFFLINE=1`, `-m real`): `29 passed, 13 skipped` (Apple, Vision OCR, regions, GigaAM on MPS;
  skipped: the Linux/Windows backend tests);
- no regression: the bash `vidx` of `languages` and the Python `vidx` were run with the real engines on
  `mix3.m4a -l uk,en,de -e whisper,parakeet,turbo`, `ru_en.m4a -l ru,en` (all engines, Apple en) and
  `ru_slides.mp4` (auto). Every `asr/*.json` (parsed), `regions.json`, `lang.txt`, `transcript.txt`,
  `aligned.md`, `ocr.md`, `frames/frames.json`, frame names and `ocr/ocr.jsonl` (parsed) are identical;
  `transcript.md` differs only in its title line (the output folder name). Raw bytes of `apple@en.json`
  and `ocr.jsonl` can differ in key order: Swift's `JSONSerialization` orders keys at random (same with
  the bash version);
- an output folder made by the bash version is fully reused (every job "cached", no engine run).

**Linux** (Docker Desktop on this Mac, `python:3.13-slim-trixie`, linux/aarch64, CPU only):

- unit + CLI: `498 passed, 3 skipped` on Python 3.13 and 3.11 (skipped: the 2 Windows tests and the
  Latin-1 locale test; the image has no such locale);
- `vidx setup --no-models` (uv tool install of the package, real uv environments): passes; device report
  `whisper, turbo, language ID: cpu int8`, `parakeet: CPUExecutionProvider`, `GigaAM: cpu`;
- real backends (`pytest -m real tests/test_real_engines.py`, whisper `tiny`): `13 passed, 1 xfailed`
  (Hindi OCR);
- whole runs (`VIDX_MODEL_WHISPER=tiny VIDX_MODEL_TURBO=tiny`, small Vosk models): `ru_slides.mp4` →
  `Привет. Сегодня короткий тест. Второй слайд про погоду.` (GigaAM base), OCR `Tecт vidx` (a Latin
  look-alike letter; Vision reads `Тест vidx`); `ru_en.m4a -l ru,en` → regions ru 0-17.63, en
  17.63-30.37, ru (macOS: 17.84, 30.4), transcript in both languages; `mix3.m4a -l uk,en,de` → regions
  6.14 / 11.26 (macOS: 6.24 / 11.36), `-e apple` → "not available on this platform", exit 0; a second
  run of ru_en: every job cached; `vidx find` works.

**Windows** (GitHub Actions `windows-2025`, x64, CPU only, 2026-10-16; no desktop machine yet):

- the first `ci` run (38964699716) failed on Windows only, 35 tests on 3.11 and 3.14; macOS and Ubuntu were
  green. Causes and fixes:
  - *code page* (31 tests): the tests wrote fixture JSON and read transcripts with `read_text()` /
    `write_text()` in cp1252 (UnicodeEncodeError on Cyrillic/CJK; German/Italian written as cp1252 bytes
    that merge.py rightly rejected). vidx itself was correct. The text-file rule of `test_text_io.py` now
    covers `tests/` too; goldens compare the bytes vidx wrote (LF), not a newline-translated read;
  - *CRLF*: `log.setup_streams` made redirected stdout/stderr UTF-8 but left Windows' CRLF translation;
    it now sets LF too (product). A test child's bare `print()` writes CRLF on Windows; `Context.call`
    copies bytes, and LF is the printing script's job (`common.unix_stdout`), so the test's child now
    switches to LF like the lib scripts;
  - *appends are not atomic on Windows*: the C runtime appends by seeking to the end and then writing, so
    the fake engines' shared call log and the job tests' `runs.log` lost and garbled lines when jobs
    finished together (`test_jobs_limit[2]`: a half-overwritten JSON line, 5 calls instead of 6;
    `test_limit_and_order`: job1's start missing, a missing `end`). Each fake call and stamp job now writes
    its own file. The product had the same pattern: `.vidx/failed` is appended from the `-j N` worker
    threads, so two jobs failing together could drop a label (exit 0 instead of 3); it now takes a lock
    (a test emulates the seek-then-write append on every OS);
  - *Ctrl-Break with `-j 1`* (`test_ctrl_break_stops_everything_on_windows[1]` timed out, `[2]` passed):
    the main thread sat in `Popen.wait()` without a timeout, which on Windows blocks in
    `WaitForSingleObject`; Python runs the SIGBREAK/SIGINT handler only after it returns, i.e. after the
    whole engine. CTRL_BREAK_EVENT reaches vidx's group on the runner (the `-j 2` case proved it), so this
    was a product bug, not a runner limitation: every orchestrator wait (`Context.call`, the frames ffmpeg,
    `capture`) now waits in 0.5 s slices, a filtered stderr is read in a thread, and a test keeps
    `src/vidx/*.py` free of `wait()`/`communicate()` without a timeout;
  - *paths*: one test looked for `asr/parakeet.json` in a Windows path; it compares path parts now.
- `ci` after the fixes (runs 38967223300, 38967670682, 38968493209): all six jobs green; Windows
  `497 passed, 8 skipped` on 3.11 and 3.14, both Ctrl-Break cases included. Skipped on Windows, all by
  design and unchanged: the POSIX Ctrl-C test (2) and the POSIX process-tree kill test (2; Windows
  kill paths are unit-tested with a fake psutil and end-to-end by Ctrl-Break), the `sh` launcher (2), the
  symlink test (no symlink rights on the runner) and the Latin-1 locale test (macOS only). No test was
  skipped or loosened for Windows by these fixes;
- `smoke` could not be dispatched: GitHub dispatches and schedules a workflow only from the default
  branch, and `smoke.yml` exists only on `porting`. A temporary `push` trigger for `porting` ran it. First
  run (38967670649): Ubuntu green; Windows `vidx setup --no-models` passed, but all 9 RapidOCR tests
  failed: `ocr_rapid.py` printed its JSON in the pipe's code page when run by a test without
  `PYTHONUTF8=1` (exit 1 on non-Latin titles; German `ö` arrived as the cp1252 byte 0xf6). The lib
  scripts' data output is now UTF-8 + LF whoever runs them (`common.unix_stdout`). Second run
  (38968493195): both jobs green. Windows: device report `whisper, turbo, language ID: cpu int8`,
  `parakeet: CPUExecutionProvider`, `GigaAM: cpu`; real backend tests (Python 3.12 test env, engine
  venvs 3.13, whisper `tiny`) `13 passed, 1 xfailed` (Hindi OCR, as on Linux); `ru_en.m4a -l ru,en -j 2`
  → regions ru 0.0-17.6, en 17.6-30.4, ru 30.4-43.5 s (same as the Ubuntu job), transcript contains
  "суп" and "soup"; `ru_slides.mp4 -e whisper,apple` → "Apple SpeechTranscriber: not available on this
  platform", OCR text in `ocr.md`.

**Locale, `vidx find` and program output** (2026-10-17; three issues the Windows pass found and left open):

- *non-UTF-8 locale on macOS/Linux* (`LC_ALL=en_US.ISO8859-1 PYTHONUTF8=0`): 10 tests failed, and
  `vidx help` stopped at its first "—" with UnicodeEncodeError, because `log.setup_streams` reconfigured
  stdout/stderr only on Windows. Decision, now on every OS: a file or pipe gets UTF-8 + LF
  (`errors="backslashreplace"`, so an undecodable file name cannot stop vidx), the same bytes as vidx's
  files and as the Windows rule; a terminal keeps its own encoding with `errors="replace"`. Rejected:
  UTF-8 to the terminal too (a Latin-1 terminal would garble every é it can show, not only what it
  cannot), and `errors="replace"` alone (redirected output would be Latin-1, unlike vidx's files and
  unlike Windows). The Windows console takes Unicode and is unaffected;
- *`vidx find` redirected on Windows*: UTF-8 but CRLF, as find.py printed with the platform's newline.
  find.py now uses `common.unix_stdout` like the other lib scripts, and vidx prints the hits through its
  own stdout, so they follow the rule above (on a terminal too). No other lib script prints data without
  it; the scripts' stderr diagnostics, like the engines' and ffmpeg's, are left as the programs write them;
- *program output read as text*: `uv tool dir`, `nvidia-smi` and the device probes used `subprocess`
  `text=True` in the locale's encoding (the code page on Windows, where uv prints a non-ASCII path in
  UTF-8). They are read as `encoding="utf-8", errors="replace"`, and the probes print UTF-8 themselves
  (`-X utf8`: their `-I` ignores the `PYTHONUTF8=1` vidx gives its children). `test_text_io.py` now also
  requires an encoding for `subprocess` text mode, in `src/` and `tests/` (13 test calls had none);
- *CI*: a new `ci` job, `latin-1`, generates `en_US.ISO-8859-1` on ubuntu-24.04 and runs the whole suite
  with `PYTHONUTF8=0`, after checking that the locale took effect; the Latin-1 merge test, so far
  macOS-only, looks for the glibc spelling too and runs there. Its first run (38972351951) found what macOS
  cannot show, as macOS encodes argv in UTF-8 whatever the locale: Linux encodes argv in the locale's
  encoding, so a Cyrillic glossary word for whisper stopped vidx before the engine started. An argument
  the locale cannot encode now goes as UTF-8 (`jobs.os_arg`), in which vidx's Python children (UTF-8
  mode) read their argv; whatever the locale can encode, every path the OS gave vidx included, is passed
  as before. The fake engine runs with `-X utf8` like a real Python engine, and the glossary test checks
  the word arrives. The same run had two test-only failures: two tests put Cyrillic on their own command
  lines (no user can in that locale either; now `\u` escapes), and on Windows the new terminal tests'
  stand-in terminal wrote CRLF;
- results: `ci` run 38973790334 green on all seven jobs: macOS `514 passed, 2 skipped` (3.11, 3.14),
  Ubuntu `513 passed, 3 skipped` (the Windows console tests and the Latin-1 test, which runs in
  `latin-1`), Windows `508 passed, 8 skipped` (as before, the Latin-1 test included), `latin-1`
  `514 passed, 2 skipped` (the Windows console tests only). `smoke` (temporary push trigger again, removed
  afterwards): runs 38972351935 and 38973790242 green on Ubuntu and Windows; device report unchanged
  (`whisper, turbo, language ID: cpu int8`, `parakeet: CPUExecutionProvider`, `GigaAM: cpu`), real backend
  tests `13 passed, 1 xfailed`. On this Mac the suite passes in the Latin-1 locale too (3.14).

**Text arguments, engine stderr, setup probes** (2026-10-17; 10.4 item 7 and two more setup issues):

- *text arguments in a non-UTF-8 locale* (`485f8d4`): `jobs.os_arg` sent as UTF-8 only what the locale could not
  encode, so `café` (Latin-1 can encode it) reached whisper as the byte 0xe9, which its UTF-8 mode reads as
  an undecodable byte, and `vidx find out café` matched nothing. Decision: text arguments are marked
  (`jobs.Text`: the whisper prompt, the find regex, the yt-dlp URL and `--sub-langs`) and go as UTF-8 on
  POSIX in every locale; a plain str (every path) keeps the locale's bytes, so a file name the OS gave vidx
  opens as before; Windows gets Unicode either way. `Engine.argv` keeps a `Text` marked. Rejected: passing
  the text in a UTF-8 file, the environment or stdin: the engines take the macOS tools' command line
  (`mlx_whisper --initial-prompt`), so every engine and the fake would need a second way in, and yt-dlp
  takes its URL only on argv. Tests: the encoding per platform with an emulated Latin-1 `os.fsencode`; that
  the prompt, regex and URL arrive as `Text`; end to end, a glossary `café`/`Деплой` and `vidx find … café`,
  which fail in `latin-1` without the fix (macOS encodes argv in UTF-8 whatever the locale);
- *lib scripts' stderr* (`da53489`): their diagnostics followed the locale (Latin-1, the code page) and CRLF
  on Windows in a `-j` log or pipe, unlike their data. `common.unix_stderr()`, called first by every script
  run by path (a test checks), gives a redirected stderr UTF-8 + LF; a terminal keeps its own. Tested with
  whisper_fw.py's error naming `café.wav`, under `PYTHONIOENCODING=latin-1` on every OS and in the real
  Latin-1 locale;
- *engine stderr on a non-UTF-8 terminal* (`938ec26`): vidx's Python children write UTF-8, so a Latin-1
  terminal showed `cafÃ©`. When vidx's stderr is a terminal that is not UTF-8, a program's stderr now goes
  through vidx (`jobs.relay`, also for `capture`), piece by piece at `\r`/`\n` so progress lines still show
  as drawn, written in the terminal's encoding with `?`; a piece that is not UTF-8 (ffmpeg naming a Latin-1
  file) is read in the locale's encoding. Rejected: relaying on every terminal (programs would no longer
  see a terminal: colours and progress bars change for everyone, to fix a rare locale). Tested with a
  stand-in Latin-1 terminal and, on macOS and in `latin-1`, through a real pseudo-terminal;
- *a device probe's timeout* (`dc931d2`): `report_devices` caught only OSError, so a probe that hit its 120 s
  timeout ended `vidx setup` with TimeoutExpired after the environments were built; it now reports
  `unknown (timed out after 120 s)` and runs the next probe (a test with a sleeping fake probe);
- *no `-c` snippets* (`eb50086`): the device probes and the GigaAM warm-up were `python -I -c <code>`, against
  the rule in CLAUDE.md. They are `lib/device.py <engine>` and `lib/gigaam_warm.py <model>`, run by path with
  `-I -X utf8` as before (no PYTHON* variables, user site or current directory; the script puts its own
  directory back on `sys.path`). A test keeps the constant `"-c"` out of `src/vidx/*.py` and the fake hook,
  one checks every setup plan. On this Mac both scripts ran in the real `.venv-giga` (`mps`; the model
  loaded from its cache). Smoke now fails when a probe reports `unknown` (`ci: smoke fails when vidx setup
  cannot name a device`), which a broken probe script would otherwise hide;
- results: this Mac: `538 passed, 2 skipped` on 3.14 and 3.11 and in the Latin-1 locale (3.14); real
  (`-m real`) `29 passed, 13 skipped`, as before. `ci` run 38978143405 failed on Windows only, in two
  new tests: their stand-in child wrote its stderr with CRLF, which the relay passes on as it is (lib scripts
  write LF; the child now does too, `tests: the relay tests' child writes LF on Windows too`). `ci` run
  38978948169 green on all seven jobs: macOS `538 passed, 2 skipped` (3.11, 3.14), Ubuntu `535 passed,
  5 skipped` (the two Windows console tests and the three real Latin-1 tests, which run in `latin-1`), Windows
  `530 passed, 10 skipped` (as before, plus the real Latin-1 stderr and pseudo-terminal tests), `latin-1`
  `538 passed, 2 skipped` (the Windows console tests only). `smoke` (temporary push trigger, removed
  afterwards): run 38978143532 green on Ubuntu and Windows; every probe named its device (`whisper, turbo,
  language ID: cpu int8`, `parakeet: CPUExecutionProvider`, `GigaAM: cpu`), real backend tests `13 passed,
  1 xfailed`, the whole runs as before.

Not covered by the runners (section 9 still applies on a real machine): a person's Ctrl-C in a console
(the test sends CTRL_BREAK_EVENT to vidx's own group), Developer Mode symlinks, NVIDIA/CUDA and DirectML,
a non-ASCII user profile (`%LOCALAPPDATA%`, Vosk), Task Manager check for leftovers after `-j 3` Ctrl-C,
YouTube URLs.

**CI**: `actionlint` 1.7.12 (with shellcheck) reports no errors for both workflows; `ci` is green on the
three OSes and in the Latin-1 locale, `smoke` on Linux and Windows (runs above).

### 10.4 Remaining

1. Done: `ci` and `smoke` are green on Windows (10.3); the temporary `push` trigger of `smoke.yml` is
   removed again. After the merge, smoke runs nightly and on demand from `main`.
2. Section 9 on a real Windows machine (CPU, then NVIDIA), and an NVIDIA run on Linux.
3. Recalibrate merge thresholds on faster-whisper / onnx-asr output from real recordings.
4. Hindi OCR on Linux/Windows; Cyrillic look-alike letters in RapidOCR output.
5. Windows Job Objects (engines die even if vidx crashes) and Vosk with non-ASCII model paths on Windows
   (a user name in Cyrillic puts `%LOCALAPPDATA%` there; set `VIDX_HOME` to an ASCII path if it fails).
6. Old plan Phase 5.2 (a real Belarusian recording) is unchanged.
7. Done: non-UTF-8 locales on Linux (10.3): text arguments go as UTF-8 (`485f8d4`), the lib scripts' stderr is
   UTF-8 + LF when redirected (`da53489`), and engine stderr is re-encoded for a non-UTF-8 terminal
   (`938ec26`). Also done: a device probe's timeout no longer stops `vidx setup` (`dc931d2`), and the device
   probes and the GigaAM warm-up are lib scripts instead of `-c` snippets (`eb50086`).
