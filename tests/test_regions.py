"""regions.py: parakeet sentences → labelled pieces → single-language regions."""
from regions import label_sentences, merge_pieces


def no_detect(a, b, prev):
    raise AssertionError("detect must not be called for short gaps")


def test_sentences_labelled_by_script():
    sents = [(0, 4, "Привет всем"), (4.5, 8, "Hello everyone"), (8.2, 9, "...")]
    pieces = label_sentences(sents, ["ru", "en"], 10, no_detect)
    assert [p[2] for p in pieces] == ["ru", None, "en", None, "en", None]


def test_long_gap_gets_audio_detection():
    calls = []

    def detect(a, b, prev):
        calls.append((a, b))
        return "en"

    pieces = label_sentences([(0, 4, "Привет"), (7, 9, "Hello")], ["ru", "en"], 9, detect)
    assert calls == [(4, 7)]
    assert pieces[1] == (4, 7, "en")


def test_unlabelled_gap_split_between_neighbours():
    regions = merge_pieces([(0, 5, "ru"), (5, 7, None), (7, 12, "en")], ["ru", "en"])
    assert regions == [{"start": 0, "end": 6, "lang": "ru"}, {"start": 6, "end": 12, "lang": "en"}]


def test_same_labels_join_into_one_region():
    regions = merge_pieces([(0, 5, "ru"), (5, 6, None), (6, 9, "ru"), (9, 12, "en")], ["ru", "en"])
    assert regions == [{"start": 0, "end": 9, "lang": "ru"}, {"start": 9, "end": 12, "lang": "en"}]
