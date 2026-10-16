"""md(): outside text (subtitles, OCR, titles, speech) must not inject HTML, images or links, but plain
punctuation stays readable."""
import re

import pytest
from markdown_it import MarkdownIt
from merge import md

LINK = re.compile(r"(?<!\\)\[[^\]]*\][(\[:]")  # an unescaped [text]( / [text][ / [text]:


@pytest.mark.parametrize("text", [
    "усім!", "Привет! Как дела?", "a_b*c|d", "5 * 3 = 15", "100% (approx)", "snake_case_name", "C++ & Rust",
    "[draft] version", "see [1] and [2]",
])
def test_plain_text_unchanged(text):
    assert md(text) == text.replace("&", "&amp;")


@pytest.mark.parametrize("text", [
    "![x](http://t)", "look ![logo](http://evil/x.png) here", "[click](javascript:alert(1))", "[a] [b](http://t)",
    "[x][ref]", "[ref]: http://evil", "![x]",
])
def test_links_and_images_neutralised(text):
    got = md(text)
    assert not LINK.search(got), got
    assert "![" not in got, got


@pytest.mark.parametrize("text", ["<img src=x onerror=alert(1)>", "<http://t>", "</p><script>"])
def test_html_escaped(text):
    assert "<" not in md(text) and ">" not in md(text)


def test_backticks_escaped():
    assert md("`code` and ``more``") == "\\`code\\` and \\`\\`more\\`\\`"


@pytest.mark.parametrize("text, want", [
    ("# heading", "\\# heading"),
    ("- item", "\\- item"),
    ("> quote", "&gt; quote"),  # an entity is not a block quote marker
    ("+ item", "\\+ item"),
])
def test_line_start_markers_escaped(text, want):
    assert md(text) == want


# --- rendered with a real CommonMark parser: outside text must come out as the same plain text ---
CM = MarkdownIt("commonmark")
INJECTED = {"link_open", "image", "html_inline", "html_block", "code_inline", "code_block", "fence", "heading_open",
            "bullet_list_open", "ordered_list_open", "blockquote_open", "hr"}
PAYLOADS = [
    "Free prize: [click [here]](https://evil.example/phish)",  # nested brackets
    "\\[docs](https://evil.example/2)",  # a backslash that escapes the escape
    "a [b [c [d]]](https://evil.example/3) e",
    "![x](http://t)", "look ![logo](http://evil/x.png) here", "[click](javascript:alert(1))", "[a] [b](http://t)",
    "[a](<http://t>)", '[a](http://t "title")', "[x][ref] [ref]: http://evil", "[ref]: http://evil", "[x]",
    "<img src=x onerror=alert(1)>", "<http://t>", "</p><script>alert(1)</script>", "<!-- comment -->",
    "`code` and ``more``", "```", "# heading", "- item", "+ item", "* item", "> quote", "### deep",
    "Intro\n# Injected",  # a newline must not start a block
    "Title\n===", "Title\n---", "intro\n1. item", "intro\n\n    code block", "a\r\n> quote", "a\r- item",
    "усім!", "[draft] version", "see [1] and [2]", "100% & more",
]


def tokens(doc):
    res = []
    for t in CM.parse(doc):
        res += [t, *(t.children or [])]
    return res


def visible(doc):
    """The plain text a reader sees (line breaks inside a paragraph shown as spaces)."""
    return "".join(t.content if t.type == "text" else " " if t.type == "softbreak" else ""
                   for t in tokens(doc))


@pytest.mark.parametrize("text", PAYLOADS)
def test_rendered_as_the_same_plain_text(text):
    doc = f"**[00:00:00]** {md(text)}\n"
    assert not {t.type for t in tokens(doc)} & INJECTED, CM.render(doc)
    assert visible(doc) == "[00:00:00] " + re.sub(r"\r\n?|\n", " ", text)


@pytest.mark.parametrize("text", PAYLOADS)
def test_heading_text_cannot_add_blocks_or_links(text):
    doc = f"# {md(text)}\n"
    types = [t.type for t in tokens(doc)]
    assert types.count("heading_open") == 1 and not set(types) & (INJECTED - {"heading_open"}), CM.render(doc)


def test_youtube_title_and_chapters_in_transcript(tmp_path):
    """The whole transcript.md: a hostile title and chapter title stay plain text."""
    import json

    from helpers import run_merge

    out = tmp_path / "t_vidx-out"
    (out / "asr").mkdir(parents=True)
    (out / "asr" / "whisper-large-v3.json").write_text(json.dumps({"segments": [
        {"start": 1, "end": 3, "text": "Hello [there](https://evil.example/s)."}]}), encoding="utf-8", newline="\n")
    title = "Free prize: [click [here]](https://evil.example/phish)"
    (out / "info.json").write_text(json.dumps({"title": title, "webpage_url": "https://video.example/watch?v=1",
                                               "chapters": [{"start_time": 0, "title": "Intro\n# Injected"}]}),
                                   encoding="utf-8", newline="\n")
    run_merge(out, "--langs", "en")
    doc = (out / "transcript.md").read_text(encoding="utf-8")
    types = [t.type for t in tokens(doc)]
    assert types.count("heading_open") == 2  # the title and the one chapter
    assert not set(types) & (INJECTED - {"heading_open"}), CM.render(doc)
    assert title in visible(doc) and "Intro # Injected" in visible(doc)
