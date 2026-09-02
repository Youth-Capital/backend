"""What may be framed inside a lesson, and what may not.

An author pastes a link; the page puts something in an `<iframe>`. The gap
between those two sentences is where this module has to be careful, because an
iframe whose address came from user input is a way to render an arbitrary page
inside the platform — under our domain, next to our navigation, to a reader
who may be fifteen.

So the rule is not "rewrite the URL". It is: recognise the provider, pull out
an id, check the id against the alphabet and length that provider actually
uses, and *build* the address from that id. Anything not recognised stays a
plain link.

The refusals below are the point of the file. A URL that merely mentions
youtube.com must not produce an embed.
"""

import pytest

from apps.learning.video import describe_video

ID = "dQw4w9WgXcQ"  # eleven characters, the real YouTube shape


# -- what should embed -----------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={ID}",
        f"https://youtube.com/watch?v={ID}",
        f"https://m.youtube.com/watch?v={ID}",
        f"https://youtu.be/{ID}",
        f"https://www.youtube.com/embed/{ID}",
        f"https://www.youtube.com/shorts/{ID}",
        f"https://www.youtube.com/live/{ID}",
        f"https://www.youtube.com/watch?v={ID}&list=PLabc&index=2",
    ],
)
def test_every_shape_youtube_hands_out_is_understood(url):
    """People paste what the address bar gave them, not a canonical form."""
    result = describe_video(url)

    assert result["provider"] == "youtube"
    assert result["video_id"] == ID
    assert result["embed_url"].startswith(
        f"https://www.youtube-nocookie.com/embed/{ID}"
    )


def test_the_player_is_the_no_cookie_host():
    """A lesson page a minor reads should not set advertising cookies."""
    result = describe_video(f"https://www.youtube.com/watch?v={ID}")

    assert "youtube-nocookie.com" in result["embed_url"]
    assert "//www.youtube.com/embed" not in result["embed_url"]


@pytest.mark.parametrize(
    "url,expected",
    [
        (f"https://youtu.be/{ID}?t=90", 90),
        (f"https://www.youtube.com/watch?v={ID}&t=1m30s", 90),
        (f"https://www.youtube.com/watch?v={ID}&t=1h2m10s", 3730),
        (f"https://www.youtube.com/watch?v={ID}&start=45", 45),
    ],
)
def test_a_timestamp_survives(url, expected):
    """"Watch from 1:30" is part of what the author chose."""
    result = describe_video(url)

    assert result["start"] == expected
    assert f"start={expected}" in result["embed_url"]


def test_nonsense_in_the_timestamp_is_ignored_not_echoed(url=None):
    result = describe_video(f"https://www.youtube.com/watch?v={ID}&t=drop-table")

    assert result["start"] == 0
    assert "start=" not in result["embed_url"]


def test_vimeo_is_also_understood():
    result = describe_video("https://vimeo.com/123456789")

    assert result["provider"] == "vimeo"
    assert result["embed_url"] == "https://player.vimeo.com/video/123456789"


# -- what must never embed -------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        # A host that merely ends in something familiar.
        f"https://youtube.com.evil.example/watch?v={ID}",
        f"https://evil.example/youtube.com/watch?v={ID}",
        # The right host, an id of the wrong shape.
        "https://www.youtube.com/watch?v=../../admin",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=waytoolongtobeanidatall",
        # No id at all.
        "https://www.youtube.com/",
        "https://www.youtube.com/results?search_query=python",
    ],
)
def test_a_url_that_only_looks_like_youtube_is_not_framed(url):
    result = describe_video(url)

    assert result["embed_url"] is None, f"would have framed: {url}"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(document.cookie)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
    ],
)
def test_a_non_http_scheme_never_becomes_an_embed(url):
    result = describe_video(url)

    assert result["embed_url"] is None


def test_an_ordinary_file_stays_a_link():
    """Our own mp4 is fine — it is simply not something to put in a frame."""
    result = describe_video("https://cdn.example.uz/lesson-1.mp4")

    assert result["provider"] == "file"
    assert result["embed_url"] is None
    assert result["url"] == "https://cdn.example.uz/lesson-1.mp4"


def test_no_video_is_no_description():
    assert describe_video("") is None
