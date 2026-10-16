"""Options of a run (`vidx <file|url> [options]`).

The same loop as the bash entry point had, message for message: the first argument is always the input,
an option's value is the next argument whatever it looks like, numbers keep the text they were given (the
step stamps contain them), and nothing like argparse's --opt=value or prefix abbreviations is accepted.
"""
import os
import re
from dataclasses import dataclass, field

ALL_ENGINES = "whisper,parakeet,gigaam,turbo,apple,vosk"
LANG_RE = re.compile(r"[a-z]{2,3}(,[a-z]{2,3})*")
NUMBER = re.compile(r"[0-9]+(\.[0-9]+)?")
APPLE_TIMEOUT_DEFAULT = "300"  # seconds for Apple's speech model download (it can hang); the only default


class Usage(Exception):
    """A usage error: vidx prints "error: <message>" and exits 1."""


class ShowHelp(Exception):
    """-h / --help among the run options."""


@dataclass
class RunOptions:
    input: str
    out: str = ""
    lang: str = "auto"
    engines: str = ALL_ENGINES
    glossary: str = ""
    scene: str = "0.3"
    every: str = "10"
    dedup: str = "6"
    roi: str = "1"
    crop: str = "0"
    maxh: str = "1080"
    force: bool = False
    jobs: int = 1
    sub_langs: str = ""
    cuts: list = field(default_factory=list)  # "start-end" in seconds, formatted like awk
    do_asr: bool = True
    do_frames: bool = True
    do_ocr: bool = True
    do_subs: bool = True
    apple_timeout: str = APPLE_TIMEOUT_DEFAULT

    def engine_list(self):
        return split_list(self.engines)

    def has(self, engine):
        return f",{engine}," in f",{self.engines},"


def split_list(text):
    """Comma list → items, as bash's `IFS=, read -a`: empty items stay, one trailing empty item does not."""
    items = text.split(",")
    return items[:-1] if items and items[-1] == "" else items


def uniq_list(text):
    """ru,en,ru → ru,en (first occurrence kept)."""
    return ",".join(dict.fromkeys(text.split(",")))


def awk_number(text):
    """A string as awk reads it: the longest number prefix, 0 if none."""
    m = re.match(r"[0-9]*\.?[0-9]*", text).group()
    return float(m) if m not in ("", ".") else 0.0


def awk_format(value):
    """A number as awk prints it: integers exactly, others with %.6g."""
    return str(int(value)) if value == int(value) and abs(value) < 1e15 else f"{value:.6g}"


def secs_text(text):
    """[h:]m:s → seconds, formatted like the bash version's awk did."""
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + awk_number(part)
    return awk_format(total)


def parse_run(args, env=None):
    """argv after `vidx` (input first) → RunOptions; Usage on a bad option, ShowHelp for -h."""
    env = os.environ if env is None else env
    o = RunOptions(input=args[0])
    rest = args[1:]
    i = 0

    def value():
        if i + 1 >= len(rest) or rest[i + 1] == "":
            raise Usage(f"option {rest[i]} needs a value")
        return rest[i + 1]

    def number():
        v = value()
        if not NUMBER.fullmatch(v):
            raise Usage(f"{rest[i]} must be a number, got '{v}'")
        return v

    while i < len(rest):
        a = rest[i]
        takes_value = True
        if a in ("-o", "--out"):
            o.out = value()
        elif a in ("-l", "--lang"):
            o.lang = value()
        elif a in ("-e", "--engines"):
            o.engines = value()
        elif a in ("-j", "--jobs"):
            o.jobs = value()
        elif a in ("-g", "--glossary"):
            v = value()
            if not os.path.isfile(v):
                raise Usage(f"no such glossary: {v}")
            o.glossary = os.path.realpath(v)
        elif a == "--scene":
            o.scene = number()
        elif a == "--every":
            o.every = number()
        elif a == "--dedup":
            o.dedup = number()
        elif a == "--roi":
            o.roi = number()
        elif a == "--max-height":
            o.maxh = number()
        elif a == "--cut":
            v = value()
            if not re.fullmatch(r"[0-9:.]+-[0-9:.]+", v):
                raise Usage("--cut needs START-END, e.g. 36:30-50:00")
            start, end = secs_text(v.split("-")[0]), secs_text(v.split("-")[-1])
            if not float(start) < float(end):
                raise Usage(f"--cut {v}: start must be before end")
            o.cuts.append(f"{start}-{end}")
        elif a == "--sub-langs":
            o.sub_langs = value()
        else:
            takes_value = False
            if a == "--crop":
                o.crop = "1"
            elif a == "--meet":
                o.roi, o.crop = "0.75", "1"
            elif a == "--no-asr":
                o.do_asr = False
            elif a == "--no-frames":
                o.do_frames = False
            elif a == "--no-ocr":
                o.do_ocr = False
            elif a == "--no-subs":
                o.do_subs = False
            elif a in ("-f", "--force"):
                o.force = True
            elif a in ("-h", "--help"):
                raise ShowHelp
            else:
                raise Usage(f"unknown option {a} (see: vidx help run)")
        i += 2 if takes_value else 1

    if not re.fullmatch(r"[1-9][0-9]*", str(o.jobs)):
        raise Usage("--jobs must be a positive number")
    o.jobs = int(o.jobs)
    o.apple_timeout = env.get("VIDX_APPLE_TIMEOUT") or APPLE_TIMEOUT_DEFAULT
    if not (re.fullmatch(r"[1-9][0-9]{0,4}", o.apple_timeout) and int(o.apple_timeout) <= 86400):
        raise Usage(f"VIDX_APPLE_TIMEOUT must be 1..86400 seconds, got '{o.apple_timeout}'")
    if o.lang != "auto" and not LANG_RE.fullmatch(o.lang):
        raise Usage("--lang must be auto or a list like ru or ru,en")
    o.lang = uniq_list(o.lang)  # a repeated language would queue two writers of one output file
    for e in o.engine_list():
        if f",{e}," not in f",{ALL_ENGINES},":
            raise Usage(f"unknown engine '{e}' (known: {ALL_ENGINES})")
    return o
