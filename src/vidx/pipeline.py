"""A vidx run: source, audio, language, speech + frames/OCR (job queue), merge. README.md describes each step.

A port of the bash entry point, step for step: the same messages, files, stamps and exit status. Every
external program runs through vidx.jobs (own process group, killed on Ctrl-C); every engine through the
registry (vidx.engines), so the same code runs on macOS, Linux and Windows.
"""
import glob
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from vidx import jobs, paths
from vidx.engines import Registry
from vidx.log import Fatal, log, warn
from vidx.options import LANG_RE
from vidx.state import OutDir

VOSK_URL = "https://alphacephei.com/vosk/models/"
ARG_CHARS = 24000  # Windows command lines are limited to 32767 characters: OCR runs in batches below this


def languages_module():
    """lib/languages.py, loaded from its file (it is also a script, run by other interpreters)."""
    spec = importlib.util.spec_from_file_location("vidx_languages", paths.LIB / "languages.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Runtime:
    """Everything a run needs from the machine: platform, engines, helper Python, tools. tests/fakes/hook.py
    replaces parts of it (VIDX_FAKE_ENGINES=1), as the bash version's hook replaced its functions."""

    def __init__(self, env=None):
        self.env = os.environ if env is None else env
        self.platform = paths.platform_of()
        self.home = paths.home(self.env, self.platform)
        self.registry = Registry(self.platform, self.home, env=self.env)
        self.fake = False
        if self.env.get("VIDX_FAKE_ENGINES") == "1":
            self.apply_fake_hook()

    def apply_fake_hook(self):
        root = paths.checkout()
        hook = root / "tests" / "fakes" / "hook.py" if root else None
        if not hook or not hook.is_file():
            raise Fatal("VIDX_FAKE_ENGINES=1 but tests/fakes/hook.py is missing")
        spec = importlib.util.spec_from_file_location("vidx_fake_hook", hook)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.apply(self)
        self.fake = True

    @property
    def helper_python(self):
        return self.registry.helper_python

    def which(self, tool):
        return shutil.which(tool)

    def build_swift(self, name):
        """name → <home>/bin/<name>, rebuilt when the .swift source is newer (macOS only)."""
        exe, src = os.path.join(self.registry.bin, name), paths.LIB / f"{name}.swift"
        if os.access(exe, os.X_OK) and os.path.getmtime(exe) > os.path.getmtime(src):
            return True
        if not shutil.which("swiftc"):
            warn(f"swiftc not found (xcode-select --install): {name} unavailable")
            return False
        os.makedirs(self.registry.bin, exist_ok=True)
        return jobs.Context().call(["swiftc", "-O", "-o", exe, str(src)]) == 0

    def fetch_vosk(self, name, base_url=VOSK_URL):
        return fetch_vosk(name, self.registry.models, base_url)


def fetch_vosk(name, models, base_url=VOSK_URL):
    """Download + unzip a Vosk model into a temp dir inside models/, then move it into place, so a half-done
    model never counts. Every zip member must stay inside the temp dir."""
    if not name:
        return False
    if os.path.isdir(os.path.join(models, name)):
        return True
    log(f"downloading Vosk model {name} (0.3-2 GB, first use only)")
    os.makedirs(models, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".tmp.", dir=models)
    try:
        archive = os.path.join(tmp, "m.zip")
        with urllib.request.urlopen(base_url + name + ".zip") as r, open(archive, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        root = os.path.realpath(tmp)
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():
                target = os.path.realpath(os.path.join(tmp, member))
                if os.path.commonpath([root, target]) != root:
                    raise ValueError(f"unsafe path in the archive: {member}")
            z.extractall(tmp)
        if not os.path.isdir(os.path.join(tmp, name)):
            raise ValueError(f"no {name}/ in the archive")
        os.replace(os.path.join(tmp, name), os.path.join(models, name))
        return True
    except (OSError, ValueError, zipfile.BadZipFile) as e:
        warn(f"Vosk model {name}: download failed ({e})")
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remove_sources(out):
    for f in glob.glob(glob.escape(out.path) + os.sep + "source.*"):
        if os.path.islink(f) or os.path.isfile(f):
            os.remove(f)


def capture(argv, quiet=False):
    """Run a program (registered for Ctrl-C), return (exit status, stdout text); quiet: no stderr. A non-UTF-8
    terminal gets the program's stderr re-encoded, after it ends (jobs.reencodes)."""
    through = not quiet and jobs.reencodes(sys.stderr)
    p = jobs.spawn(argv, stdout=subprocess.PIPE,
                   stderr=subprocess.DEVNULL if quiet else subprocess.PIPE if through else None)
    out, err = jobs.communicate(p)
    jobs._forget(p)
    if err:
        jobs.relay(io.BytesIO(err), sys.stderr)
    return p.returncode, out.decode("utf-8", "replace")


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Run:
    def __init__(self, opts, rt):
        self.o, self.rt, self.reg = opts, rt, rt.registry
        self.L = languages_module()
        self.queue, self.sources = [], []
        self.apple_locales = None
        self.prompt = ""

    # --- helpers ---------------------------------------------------------------------------------------------
    def need(self, tool):
        path = self.rt.which(tool)
        if not path:
            raise Fatal(f"{tool} not found — run: vidx setup")
        return path

    def has(self, engine):
        return self.o.has(engine)

    def tag(self, stem, lang):
        return f"{stem}@{lang}" if self.mixed else stem

    def asr(self, stem):
        return self.out.join("asr", stem + ".json")

    def add(self, label, output, params, body):
        self.queue.append(jobs.Job(label, output, params, body))

    def add_asr(self, label, output, params, body):
        """Like add, for an engine output in asr/: merge reads it."""
        self.sources.append(os.path.basename(output)[:-5])
        self.add(label, output, params, body)

    def has_stream(self, kind):
        rc, out = capture([self.ffprobe, "-v", "error", "-select_streams", kind, "-show_entries", "stream=codec_type",
                           "-of", "csv=p=0", self.src])
        return bool(out.strip())

    # --- the run ---------------------------------------------------------------------------------------------
    def main(self):
        o = self.o
        self.ffmpeg, self.ffprobe = self.need("ffmpeg"), self.rt.which("ffprobe") or "ffprobe"
        if not os.path.isfile(self.rt.helper_python):
            raise Fatal("helper venv missing — run: vidx setup")
        self.source()
        log(f"output: {self.out.path}")
        self.out.reset_failed()
        self.audio_and_language()
        self.speech_and_pictures()
        jobs.run_queue(self.queue, o.jobs, self.out)
        return self.merge()

    def source(self):
        o = self.o
        if re.match(r"https?://", o.input):
            self.download()
            return
        inp = o.input
        if not os.path.isfile(inp):
            raise Fatal(f"no such file: {inp}")
        if inp.startswith("-"):
            inp = "./" + inp
        base = os.path.basename(inp)
        ext = base.rsplit(".", 1)[1] if "." in base else "video"
        out = o.out or f"./{base.rsplit('.', 1)[0] if '.' in base else base}_vidx-out"
        real = os.path.realpath(inp)
        self.out = OutDir.prepare(out, o.force)
        self.out.same_input(real)
        remove_sources(self.out)
        self.src = self.out.join(f"source.{ext}")
        try:
            os.symlink(real, self.src)
        except (OSError, NotImplementedError):  # Windows without the symlink right (Developer Mode or admin)
            log(f"input read in place (no symlink allowed here): {real}")
            self.src = real

    def download(self):
        o = self.o
        ytdlp = [self.need("yt-dlp"), "--ignore-config", "--no-warnings", "--no-playlist"]
        out = o.out
        if not out:
            rc, text = capture([*ytdlp, "--skip-download", "--print", "%(id)s", jobs.Text(o.input)])
            vid = re.sub(r"[^A-Za-z0-9_-]", "_", (text.splitlines() or [""])[0]) if rc == 0 else ""
            if not vid:
                raise Fatal(f"could not read the video id of {o.input}")
            out = f"./{vid}_vidx-out"
        self.out = OutDir.prepare(out, o.force)
        self.out.same_input(o.input)
        out_t = self.out.path.replace("%", "%%")  # yt-dlp output templates treat % specially
        self.src = self.out.find_source()
        run = jobs.Context().call
        if self.out.force or not self.src:
            log(f"downloading video (≤{o.maxh}p) + metadata")
            remove_sources(self.out)
            sep = os.sep
            rc = run([*ytdlp, "--force-overwrites", "-f", f"bv*[height<={o.maxh}]+ba/b[height<={o.maxh}]/b",
                      "--merge-output-format", "mp4", "-o", f"{out_t}{sep}source.%(ext)s",
                      "--write-info-json", "--write-description", "--write-thumbnail", "--convert-thumbnails", "jpg",
                      "-o", f"infojson:{out_t}{sep}info", "-o", f"description:{out_t}{sep}description",
                      "-o", f"thumbnail:{out_t}{sep}thumbnail", jobs.Text(o.input)])
            # yt-dlp appends its own extensions: info.info.json, description.description
            for a, b in (("info.info.json", "info.json"), ("description.description", "description.txt")):
                if os.path.isfile(self.out.join(a)):
                    os.replace(self.out.join(a), self.out.join(b))
            self.src = self.out.find_source()
            if rc != 0 or not self.src:
                raise Fatal("download failed")
        if o.do_subs and (self.out.force or not glob.glob(glob.escape(self.out.join("subs")) + os.sep + "*.json3")):
            # Two passes so manual and auto captions land in separate files. Only original-language tracks:
            # ru, en, the --lang list and the video's own language. An empty entry would be a match-all regex
            # (every machine translation), so the list is built without one.
            log("downloading YouTube subtitles (manual + auto)")
            shutil.rmtree(self.out.join("subs"), ignore_errors=True)
            os.makedirs(self.out.join("subs"))
            vl = (read_json(self.out.join("info.json"), {}) or {}).get("language") or ""
            vl = vl if isinstance(vl, str) and re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]+)?", vl) else ""
            sl = o.sub_langs
            if not sl:
                for lang in ["ru", "en", *o.lang.split(","), *([vl] if vl else [])]:
                    if lang != "auto" and f",{lang}," not in f",{sl},":
                        sl = f"{sl},{lang},{lang}-orig" if sl else f"{lang},{lang}-orig"
            for kind, flag in (("manual", "--write-subs"), ("auto", "--write-auto-subs")):
                if run([*ytdlp, "-q", "--skip-download", flag, "--sub-langs", jobs.Text(sl), "--sub-format", "json3",
                        "-o", f"subtitle:{out_t}{os.sep}subs{os.sep}{kind}", jobs.Text(o.input)]) != 0:
                    warn(f"{kind} subtitles failed")

    def audio_and_language(self):
        o = self.o
        self.audio = self.out.join("audio.wav")
        if o.do_asr:
            if not self.has_stream("a"):
                raise Fatal(f"no audio track in {self.src} (use --no-asr for pictures only)")
            if self.out.fresh(self.audio, "16k-mono"):
                log("extracting audio (16 kHz mono)")
                tmp = self.out.join("audio.tmp.wav")
                if jobs.Context().call([self.ffmpeg, "-nostdin", "-loglevel", "error", "-y", "-i", self.src, "-vn",
                                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp]) != 0:
                    raise Fatal("audio extraction failed (ffmpeg)")
                os.replace(tmp, self.audio)
                self.out.stamp(self.audio, "16k-mono")
            _, dur = capture([self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              self.audio])
            d = int(dur.strip().split(".")[0] or 0)
            log(f"duration: {d // 60}:{d % 60:02d}")
            if o.lang == "auto":
                lang_txt = self.out.join("lang.txt")
                if self.out.fresh(lang_txt, "detect"):
                    log("detecting language (10 s clips, 1 per minute, whisper turbo)")
                    tmp = self.out.join("lang.tmp")
                    if jobs.Context().call(self.reg.lid_argv("detect_lang", self.audio), stdout=tmp,
                                           drop=self.reg.lid.drop) != 0:
                        raise Fatal("language detection failed")
                    os.replace(tmp, lang_txt)
                    self.out.stamp(lang_txt, "detect")
                with open(lang_txt, encoding="utf-8") as f:
                    langs = f.read().strip()
            else:
                langs = o.lang
            if not LANG_RE.fullmatch(langs):
                raise Fatal(f"bad language list '{langs}' (lang.txt?)")
        else:
            langs = o.lang
            if langs == "auto":  # no speech run: the YouTube track language decides, else ru
                langs = (read_json(self.out.join("info.json"), {}) or {}).get("language") or "ru"
                if not isinstance(langs, str) or not LANG_RE.fullmatch(langs):
                    langs = "ru"
        self.langs = langs.split(",")
        self.ocr_langs = self.L.ocr_arg(self.langs)  # OCR: the run's languages + English, one group per script
        self.mixed = len(self.langs) > 1
        if o.do_asr:
            log(f"language: {','.join(self.langs)}"
                + (" (mixed: language-bound engines run once per language)" if self.mixed else ""))

    # --- 4. speech, 5. pictures --------------------------------------------------------------------------------
    def clips_of(self, lang):
        """"start,end,start,end" of the language's regions; "" = whole file; "none" = no region."""
        if not self.mixed:
            return ""
        regions = read_json(self.out.join("regions.json"), [])
        r = [f"{x['start']},{x['end']}" for x in regions if x["lang"] == lang]  # str() of the JSON numbers
        return ",".join(r) or "none"

    def single_language(self, why):
        warn(f"{why}: continuing with '{self.langs[0]}' only")
        self.langs, self.mixed = self.langs[:1], False

    def run_now(self, label, output, params, body):
        """A job that must finish before the queue is built (parakeet before the regions)."""
        jobs.run_job(jobs.Job(label, output, params, body), self.out, background=False)

    def engine_job(self, engine, *args):
        return jobs.command(engine.argv(*args), drop=engine.drop, env=engine.env)

    def parakeet_job(self):
        p = self.reg.get("parakeet")
        return self.engine_job(p, self.audio, "--model", p.model, "--output-format", "json", "--output-dir",
                               self.out.join("asr"), "--output-template", "parakeet")

    def whisper_job(self, engine, stem, lang, clips):
        args = ["--model", engine.model, "-o", self.out.join("asr"), "--output-name", stem, "-f", "json",
                "--verbose", "False", "--condition-on-previous-text", "False", "--language", lang]
        if self.prompt:
            args += ["--initial-prompt", jobs.Text(self.prompt)]  # text: UTF-8 in every locale
        if clips:
            args += ["--clip-timestamps", clips]
        return self.engine_job(engine, self.audio, *args)

    def unavailable(self, name, label):
        if self.has(name) and self.reg.get(name) is None:
            log(f"{label}: not available on this platform, skipped")
            return True
        return not self.has(name)

    def speech_and_pictures(self):
        o = self.o
        if o.glossary:
            with open(o.glossary, encoding="utf-8-sig") as f:
                self.prompt = next((m[1] for line in f if (m := re.match(r"prompt: *(.*)", line.rstrip("\r\n")))),
                                   "")
        rows = [self.L.get(lang) for lang in self.langs]
        # Mixed speech: regions.py splits the audio into single-language regions by whisper turbo's language ID
        # on short pieces; parakeet (no language forced) runs first when enabled and its sentence edges sharpen
        # the boundaries. It must run before the regions exist, so it is gated by the LISTED languages.
        if o.do_asr and self.mixed:
            os.makedirs(self.out.join("asr"), exist_ok=True)
            region_args, snap = [",".join(self.langs), self.audio, self.out.join("regions.tmp")], "none"
            if not self.unavailable("parakeet", "parakeet"):
                if not all(r.parakeet for r in rows):
                    log(f"parakeet: not every language of {','.join(self.langs)} is one of its 25, skipped")
                else:
                    self.sources = ["parakeet"]
                    self.run_now("parakeet-tdt-0.6b-v3 [any]", self.asr("parakeet"), self.reg.get("parakeet").model,
                                 self.parakeet_job())
                    if os.path.isfile(self.asr("parakeet")) and os.path.getsize(self.asr("parakeet")):
                        region_args.append(self.asr("parakeet"))
                        snap = "parakeet"
            params = f"{','.join(self.langs)} lid snap={snap}"  # boundaries snapped to parakeet's sentences or not
            regions = self.out.join("regions.json")
            if self.out.fresh(regions, params):
                log("splitting into language regions (whisper turbo language ID)")
                if jobs.Context().call(self.reg.lid_argv("regions", *region_args), drop=self.reg.lid.drop) == 0:
                    os.replace(self.out.join("regions.tmp"), regions)
                    self.out.stamp(regions, params)
                else:
                    self.single_language("language split failed")
        regions_text = ""
        if o.do_asr and self.mixed:
            with open(self.out.join("regions.json"), encoding="utf-8", newline="") as f:
                regions_text = f.read().rstrip("\n")
        clips = [self.clips_of(lang) for lang in self.langs] if o.do_asr else ["" for _ in self.langs]

        # Queue order interleaves GPU and CPU work, so -j 2/3 keeps both busy:
        # whisper (GPU) · vosk (CPU) · frames+OCR (CPU) · gigaam · parakeet · apple · turbo
        def queue_whisper(name, label, stem):
            if self.unavailable(name, label):
                return
            engine = self.reg.get(name)
            for lang, c in zip(self.langs, clips, strict=True):
                s = self.tag(stem, lang)
                if c == "none":
                    log(f"{label} [{lang}]: no {lang} regions, skipped")
                else:
                    self.add_asr(f"{label} [{lang}]", self.asr(s), f"{engine.model} {lang} {c} {self.prompt}",
                                 self.whisper_job(engine, s, lang, c))

        if o.do_asr:
            os.makedirs(self.out.join("asr"), exist_ok=True)
            queue_whisper("whisper", "whisper large-v3", "whisper-large-v3")
            if not self.unavailable("vosk", "Vosk"):
                for lang, row, c in zip(self.langs, rows, clips, strict=False):
                    s, m = self.tag("vosk", lang), self.rt.env.get(f"VIDX_VOSK_{lang.upper()}") or row.vosk_model
                    if c == "none":
                        log(f"Vosk [{lang}]: no {lang} regions, skipped")
                    elif not m:
                        log(f"Vosk: no model for '{lang}', skipped")
                    elif self.rt.fetch_vosk(m):
                        self.add_asr(f"Vosk [{lang}] (CPU)", self.asr(s), m,
                                     self.engine_job(self.reg.get("vosk"), os.path.join(self.reg.models, m),
                                                     self.audio, self.asr(s)))
                    else:
                        log(f"Vosk [{lang}]: model download failed, skipped")
        if o.do_frames and self.has_stream("v"):
            self.add("frames + OCR", "", "", self.frames_and_ocr)
        if o.do_asr:
            if not self.unavailable("gigaam", "GigaAM"):
                if "ru" not in self.langs:
                    log("GigaAM: Russian only, skipped")
                elif clips[self.langs.index("ru")] == "none":
                    log("GigaAM [ru]: no ru regions, skipped")
                else:
                    args = [self.audio, self.asr("gigaam")] + ([self.out.join("regions.json")] if self.mixed else [])
                    g = self.reg.get("gigaam")
                    self.add_asr("GigaAM-v3 [ru]", self.asr("gigaam"), f"{g.model} {regions_text}",
                                 self.engine_job(g, *args))
            if not self.mixed and not self.unavailable("parakeet", "parakeet"):
                if rows[0].parakeet:
                    self.add_asr("parakeet-tdt-0.6b-v3 [any]", self.asr("parakeet"), self.reg.get("parakeet").model,
                                 self.parakeet_job())
                else:
                    log(f"parakeet: no '{','.join(self.langs)}', skipped (25 European languages only)")
            if not self.unavailable("apple", "Apple SpeechTranscriber") and self.rt.build_swift("apple_asr"):
                for lang, row, c in zip(self.langs, rows, clips, strict=False):
                    loc, s = row.apple_locale, self.tag("apple", lang)
                    if not loc:
                        log(f"Apple SpeechTranscriber: no '{lang}', skipped")
                    elif c == "none":
                        log(f"Apple SpeechTranscriber [{lang}]: no {lang} regions, skipped")
                    elif self.apple_supports(loc):
                        self.add_asr(f"Apple SpeechTranscriber [{lang}]", self.asr(s), loc,
                                     self.apple_job(loc, self.asr(s)))
                    else:
                        log(f"Apple SpeechTranscriber: no {loc}, skipped")
            queue_whisper("turbo", "whisper turbo", "whisper-turbo")

    def apple_supports(self, locale):
        """Asks apple_asr once per run which locales it supports."""
        if self.apple_locales is None:
            _, text = capture(self.reg.get("apple").argv("--locales"), quiet=True)
            self.apple_locales = next((line.split()[1:] for line in text.splitlines() if line.startswith("supported:")),
                                      [])
        return locale in self.apple_locales

    def apple_job(self, locale, out):
        apple, timeout = self.reg.get("apple"), self.o.apple_timeout

        def body(ctx):  # exit 4 = the speech model download timed out
            rc = ctx.call(apple.argv("--timeout", timeout, locale, self.audio, out))
            if rc == 4:
                msg = f"Apple SpeechTranscriber: model download for {locale} timed out after {timeout} s"
                warn(msg) if ctx.stream is None else ctx.say(f"warn: {msg}")
            return rc == 0

        return body

    def frames_and_ocr(self, ctx):
        o, out = self.o, self.out
        fp = f"scene={o.scene} every={o.every} dedup={o.dedup} roi={o.roi} crop={o.crop}"
        frames_json = out.join("frames", "frames.json")
        if out.fresh(frames_json, fp):
            ctx.say(f"frames: scene change > {o.scene} or every {o.every}s")
            shutil.rmtree(out.join("frames"), ignore_errors=True)
            shutil.rmtree(out.join("ocr"), ignore_errors=True)
            os.makedirs(out.join("frames"))
            raw = tempfile.mkdtemp()  # outside OUT; % escaped too: ffmpeg's %06d pattern
            try:
                pattern = os.path.join(raw.replace("%", "%%"), "%06d.jpg")
                vf = (f"select='isnan(prev_selected_t)+gt(scene\\,{o.scene})+gte(t-prev_selected_t\\,{o.every})',"
                      "showinfo")
                showinfo = os.path.join(raw, "showinfo.log")
                with open(showinfo, "wb") as log_file:
                    p = jobs.spawn([self.ffmpeg, "-nostdin", "-hide_banner", "-y", "-i", self.src, "-an", "-vf", vf,
                                    "-fps_mode", "vfr", "-q:v", "2", pattern], stdout=ctx.stream, stderr=log_file)
                    rc = jobs.wait(p)
                    jobs._forget(p)
                if rc != 0 or ctx.call([self.rt.helper_python, str(paths.LIB / "frames.py"), raw, showinfo,
                                        out.join("frames"), o.dedup, o.roi, o.crop]) != 0:
                    return False
            finally:
                shutil.rmtree(raw, ignore_errors=True)
            out.stamp(frames_json, fp)
        ocr_out = out.join("ocr", "ocr.jsonl")
        if o.do_ocr and out.fresh(ocr_out, self.ocr_langs):
            if self.platform_needs_swift() and not self.rt.build_swift("ocr"):
                return False
            files = sorted(glob.glob(glob.escape(out.join("frames", "unique")) + os.sep + "*.jpg"))
            ocr = self.reg.get("ocr")
            ctx.say(f"OCR ({ocr.model}, {self.ocr_langs}) on {len(files)} frames")
            os.makedirs(out.join("ocr"), exist_ok=True)
            tmp = out.join("ocr", "ocr.tmp")
            open(tmp, "wb").close()
            for batch in batches(files):
                part = tmp + ".part"
                rc = ctx.call(ocr.argv(self.ocr_langs, *batch), stdout=part, env=ocr.env)
                with open(part, "rb") as f, open(tmp, "ab") as t:
                    t.write(f.read())
                os.remove(part)
                if rc != 0:
                    return False
            os.replace(tmp, ocr_out)
            out.stamp(ocr_out, self.ocr_langs)
        return True

    def platform_needs_swift(self):
        return self.rt.platform == "darwin"

    # --- 6. merge ----------------------------------------------------------------------------------------------
    def merge(self):
        log("merging")
        args = ["--langs", ",".join(self.langs)]
        if self.o.glossary:
            args += ["--glossary", self.o.glossary]
        if self.o.do_asr:
            args += ["--sources", ",".join(self.sources)]
        for c in self.o.cuts:
            args += ["--cut", c]
        if self.reg.get("ocr").model != "Apple Vision":
            args += ["--ocr-name", self.reg.get("ocr").model]
        if jobs.Context().call([self.rt.helper_python, str(paths.LIB / "merge.py"), self.out.path, *args]) != 0:
            raise Fatal("merge failed")
        failed = self.out.failed()
        transcript = self.out.join("transcript.md")
        if failed:
            warn(f"failed jobs: {', '.join(failed)} — see logs/ (with -j) or the output above")
            log(f"done with errors → {transcript}")
            return 3
        log(f"done → {transcript}")
        return 0


def batches(files, limit=ARG_CHARS):
    """Split a file list so that no command line gets longer than limit characters."""
    batch, size = [], 0
    for f in files:
        if batch and size + len(f) + 1 > limit:
            yield batch
            batch, size = [], 0
        batch.append(f)
        size += len(f) + 1
    if batch:
        yield batch

