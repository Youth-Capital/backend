"""Turning a pasted video link into something safe to embed.

An author pastes whatever the browser's address bar gave them — a watch page,
a share link, a link with a timestamp on it. None of those can go into an
`<iframe src>` directly, so the id is extracted here and the embed URL is
built from scratch.

Built rather than rewritten, deliberately. Handing the frontend a URL taken
from user input and letting it fill an iframe is how a lesson becomes a way to
frame an arbitrary page inside the platform. Only a provider on this list
produces an embed at all, and the address is assembled from an id that has
been checked character by character — so whatever was pasted, what reaches the
page is a URL this module wrote.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

#: YouTube ids are exactly this alphabet and this length. Anything else is not
#: an id, however convincing the URL around it looked.
YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIMEO_ID = re.compile(r"^\d{6,12}$")

YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtu.be",
}
VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}


def describe_video(url: str) -> dict | None:
    """What a lesson's video is, and how to show it.

    Returns None when there is no url. Otherwise a dict with `provider`
    ("youtube", "vimeo" or "file"), the original `url`, and an `embed_url`
    that is None for anything the platform will not frame.
    """
    if not url:
        return None

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return {"provider": "file", "url": url, "embed_url": None, "start": 0}

    if parsed.scheme not in {"http", "https"}:
        return {"provider": "file", "url": url, "embed_url": None, "start": 0}

    host = (parsed.hostname or "").lower()
    start = _start_seconds(parsed)

    if host in YOUTUBE_HOSTS:
        video_id = _youtube_id(parsed)
        if video_id:
            # youtube-nocookie: the player is on a lesson page a minor may be
            # reading, and it should not set advertising cookies to be there.
            embed = f"https://www.youtube-nocookie.com/embed/{video_id}?rel=0"
            if start:
                embed = f"{embed}&start={start}"
            return {
                "provider": "youtube",
                "url": url,
                "embed_url": embed,
                "video_id": video_id,
                "start": start,
            }

    if host in VIMEO_HOSTS:
        video_id = _vimeo_id(parsed)
        if video_id:
            return {
                "provider": "vimeo",
                "url": url,
                "embed_url": f"https://player.vimeo.com/video/{video_id}",
                "video_id": video_id,
                "start": start,
            }

    # Anything else stays a link. It may be a perfectly good mp4 on our own
    # storage; it is simply not something to put in a frame.
    return {"provider": "file", "url": url, "embed_url": None, "start": start}


def _youtube_id(parsed) -> str | None:
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""

    if host in {"youtu.be", "www.youtu.be"}:
        candidate = path.lstrip("/").split("/")[0]
        return candidate if YOUTUBE_ID.match(candidate) else None

    if path == "/watch":
        values = parse_qs(parsed.query).get("v", [])
        candidate = values[0] if values else ""
        return candidate if YOUTUBE_ID.match(candidate) else None

    # /embed/<id>, /v/<id>, /shorts/<id>, /live/<id>
    for prefix in ("/embed/", "/v/", "/shorts/", "/live/"):
        if path.startswith(prefix):
            candidate = path[len(prefix) :].split("/")[0]
            return candidate if YOUTUBE_ID.match(candidate) else None

    return None


def _vimeo_id(parsed) -> str | None:
    parts = [part for part in (parsed.path or "").split("/") if part]
    if not parts:
        return None
    candidate = parts[-1] if parts[0] != "video" else parts[-1]
    return candidate if VIMEO_ID.match(candidate) else None


def _start_seconds(parsed) -> int:
    """A `t=` or `start=` value, in seconds. 0 when absent or nonsense."""
    query = parse_qs(parsed.query)
    raw = (query.get("t") or query.get("start") or [""])[0]
    if not raw:
        return 0

    if raw.isdigit():
        return min(int(raw), 86_400)

    # "1h2m10s" and its shorter forms.
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", raw)
    if not match or not any(match.groups()):
        return 0
    hours, minutes, seconds = (int(value or 0) for value in match.groups())
    return min(hours * 3600 + minutes * 60 + seconds, 86_400)
