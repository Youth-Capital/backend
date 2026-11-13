"""Where an external vacancy may point, and what is stored when it does.

A student is sent to this address from a page carrying the platform's name, so
the address is not free text. Only the hosts in
`settings.EXTERNAL_VACANCY_PLATFORMS` are accepted, and what gets stored is
rebuilt from the parsed URL — scheme, host, path, and for the one board that
needs it, the query — so the tracking parameters a job board hangs off its own
links do not travel to every student who clicks.

The same shape as `apps.learning.external`, which does this for courses, and
for the same reasons. The two lists are separate because the answers differ:
nobody takes a course on OLX, and nobody is hired through Coursera.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.conf import settings


def _domain(host: str) -> str:
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def source_for(url: str) -> str:
    """The VacancySource value a link belongs to, or empty if none."""
    if not url:
        return ""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return settings.EXTERNAL_VACANCY_PLATFORMS.get(_domain(host), "")


def clean_vacancy_url(raw: str) -> str:
    """The address to store, or ValueError naming what is wrong with it."""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("not_a_url")
    try:
        parsed = urlparse(raw)
    except ValueError as error:
        raise ValueError("not_a_url") from error

    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("not_a_url")
    if _domain(parsed.hostname) not in settings.EXTERNAL_VACANCY_PLATFORMS:
        raise ValueError("platform_not_allowed")

    path = parsed.path.rstrip("/")
    if not path:
        # A board's front page is not a vacancy. It is also the thing a
        # mistyped link most often collapses to, which is why it is caught
        # here rather than discovered by a student.
        raise ValueError("not_a_vacancy_page")

    return f"https://{parsed.hostname.lower()}{path}"
