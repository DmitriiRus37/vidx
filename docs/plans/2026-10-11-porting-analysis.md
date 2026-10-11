# vidx on Linux and Windows: analysis and step list

Status: written 2026-10-11 on branch `porting` (from `languages`, commit `5c4b82f`), before any port code.
It refines Phases 0.2, 2, 3 and 5 of `2026-10-10-platforms-and-languages.md` against the code as it is
now. Section 10 records what was built and validated; it is updated as the steps land.

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
| `.venv-onnx` (3.13) | — | ✓ | onnx-asr + one onnxruntime flavour: `onnxruntime-gpu<1.27` (CUDA 12, the same CUDA as CTranslate2), `onnxruntime-directml` (Windows without NVIDIA), else `onnxruntime` |

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

(Filled in as the steps land.)
