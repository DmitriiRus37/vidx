"""Spoken-language detection on evenly spaced 10 s clips (runs in mlx-whisper's own Python).

usage: detect_lang.py <audio.wav 16k mono> <whisper model>
Clips: one per minute of audio, at least 5 and at most 30 (fewer if the audio is short). Each clip's
language is the most probable one from lid.py (whisper's language ID).
stdout: comma list, most frequent first. The top language always counts; any other one (no limit on
their number) only if it wins at least 2 clips AND 20 % of them, so one misdetected clip does not make
a video "mixed". So auto finds only languages with long passages; short switches need -l.
No speech at all → "ru".
"""
import collections
import sys

from common import is_silent, read_wav

CLIP_S, MIN_CLIPS, MAX_CLIPS, MIN_SHARE, MIN_VOTES = 10, 5, 30, 0.2, 2
NO_SPEECH = "ru"


def vote(votes, n):
    """Counter of detected languages over n clips → languages that count, most frequent first."""
    top = [lang for lang, _ in votes.most_common()]
    return [lang for i, lang in enumerate(top) if i == 0 or votes[lang] >= max(MIN_VOTES, MIN_SHARE * n)] or [NO_SPEECH]


def main():
    import lid

    src, model = sys.argv[1], sys.argv[2]
    detect = lid.load(model)
    sr, x = read_wav(src)
    dur = len(x) / sr
    n = max(1, min(max(MIN_CLIPS, int(dur // 60)), MAX_CLIPS, int(dur // CLIP_S)))
    votes = collections.Counter()
    for k in range(n):
        a = max(0.0, (2 * k + 1) * dur / (2 * n) - CLIP_S / 2)
        clip = x[int(a * sr):int((a + CLIP_S) * sr)]
        if is_silent(clip):  # silence says nothing about the language
            continue
        probs = detect(clip)
        votes[max(probs, key=probs.get)] += 1
    print(",".join(vote(votes, n)))
    print("  clips: " + (", ".join(f"{k} {v}" for k, v in votes.most_common()) or "no speech, assuming ru"),
          file=sys.stderr)


if __name__ == "__main__":
    main()
