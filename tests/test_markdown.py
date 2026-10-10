"""md(): outside text (subtitles, OCR, titles, speech) must not inject HTML, images or links, but plain
punctuation stays readable."""
import re

import pytest
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
