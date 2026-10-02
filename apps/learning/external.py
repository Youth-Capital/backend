"""Courses taken on another platform — which ones, and how the link is kept.

An employer may list a course that lives on Coursera rather than here. The
learner is sent to that address from a page carrying the platform's name, so
the address is not free text: only the platforms in
`settings.EXTERNAL_COURSE_PLATFORMS` are accepted, and what is stored is
rebuilt from the parsed URL — scheme, host and path — so a tracking string or
an affiliate parameter someone pasted along with it does not go to every
learner who clicks.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings


def _domain(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def platform_name(url: str) -> str:
    """"Coursera" for a Coursera link; empty for anything else, or no link."""
    if not url:
        return ""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return settings.EXTERNAL_COURSE_PLATFORMS.get(_domain(host), "")


def clean_external_url(raw: str) -> str:
    """The address to store, or ValueError saying what is wrong with it.

    Empty in, empty out: clearing the field turns the course back into one
    taken on the platform.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError as error:
        raise ValueError("not_a_url") from error

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("not_a_url")
    domain = _domain(parsed.hostname)
    if domain not in settings.EXTERNAL_COURSE_PLATFORMS:
        raise ValueError("platform_not_allowed")
    path = parsed.path.rstrip("/")
    if not path:
        # The platform's home page is not a course.
        raise ValueError("not_a_course_page")
    return f"https://{parsed.hostname.lower()}{path}"
