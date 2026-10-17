"""The output folder: the .vidx/ marker, one option stamp per step, the input it was made from, failed jobs.

.vidx/ marks a folder vidx made (so -o never wipes or overwrites someone else's files) and holds one stamp
per step: the options it ran with. A step reruns when its output is missing or empty, its stamp differs
(other options, or an interrupted run that never wrote the stamp) or with -f. The files are the ones the
bash version wrote, byte for byte, so a folder made by either version is reused by the other.
"""
import os
import re
import threading

from vidx.log import Fatal, warn

_failed_lock = threading.Lock()  # -j N: jobs fail in worker threads; a Windows append is not atomic


def printable(name):
    """A file name for a message: the bytes of a name that is not UTF-8 shown as U+FFFD, not as surrogates that a
    strict stream cannot write."""
    return name.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


class OutDir:
    def __init__(self, path, force):
        self.path, self.force = path, force

    @classmethod
    def prepare(cls, path, force):
        if os.path.exists(path) and not os.path.isdir(path):
            raise Fatal(f"{path} exists and is not a directory")
        if os.path.isdir(path) and os.listdir(path) and not os.path.isdir(os.path.join(path, ".vidx")):
            raise Fatal(f"{path} is not empty and was not made by vidx; pick another -o")
        os.makedirs(os.path.join(path, ".vidx"), exist_ok=True)
        return cls(os.path.abspath(path), force)

    def join(self, *parts):
        return os.path.join(self.path, *parts)

    def stamp_of(self, output):
        return self.join(".vidx", os.path.basename(output) + ".params")

    def fresh(self, output, params=None):
        """True if the step that makes `output` must run."""
        if self.force or not os.path.isfile(output) or os.path.getsize(output) == 0:
            return True
        if params is None:
            return False
        try:
            with open(self.stamp_of(output), encoding="utf-8", newline="") as f:
                old = f.read().rstrip("\n")  # bash compared "$(cat stamp)": trailing newlines dropped
        except OSError:
            return True
        return old != params

    def stamp(self, output, params):
        with open(self.stamp_of(output), "w", encoding="utf-8", newline="") as f:
            f.write(params)

    def same_input(self, value):
        """Remember what this folder was made from; a different input means starting over. A file name that is not
        UTF-8 (Linux: Latin-1 bytes, which Python gives as surrogates) is stored as its own bytes."""
        marker = self.join(".vidx", "input")
        try:
            with open(marker, encoding="utf-8", errors="surrogateescape", newline="") as f:
                prev = f.read()
        except OSError:
            prev = ""
        if prev and prev != value:
            warn(f"input changed ({printable(prev)} → {printable(value)}): redoing everything")
            self.force = True
        with open(marker, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(value)

    def find_source(self):
        """source.<ext> only: not yt-dlp's source.f137.mp4 / .temp.mp4 / .part leftovers."""
        for name in sorted(os.listdir(self.path)):
            p = self.join(name)
            if re.fullmatch(r"source\.[A-Za-z0-9]+", name) and os.path.exists(p):
                return p
        return None

    def reset_failed(self):
        open(self.join(".vidx", "failed"), "w", encoding="utf-8", newline="\n").close()

    def add_failed(self, label):
        with _failed_lock, open(self.join(".vidx", "failed"), "a", encoding="utf-8", newline="\n") as f:
            f.write(label + "\n")

    def failed(self):
        with open(self.join(".vidx", "failed"), encoding="utf-8") as f:
            return [line.rstrip("\n") for line in f if line.strip()]
