"""Recognising the device somebody signed in from.

A sign-in alert is only as good as its idea of "a different device", and two
mistakes pull in opposite directions. Too fine a fingerprint and every browser
update looks like an intruder, so people learn to ignore the alert — which is
worse than not sending it. Too coarse and a real intruder never trips it.

What is used here is the browser family, the operating system family and
whether the thing is a phone, a tablet or a computer. Deliberately not used:

  * version numbers, because browsers update themselves every few weeks and
    each update would otherwise read as a new device;
  * the IP address, because a phone changes its address several times a day
    between mobile data and Wi-Fi, and none of those are a new device.

The limit of this, stated plainly rather than buried: somebody signing in from
the same *kind* of setup as yours — Chrome on Windows, when you also use Chrome
on Windows — does not look new and raises nothing. This catches the case the
feature exists for, an account opened on a phone that is not yours, and it is
not device identification. A user agent is a string the client chooses, and
anything built on it is a hint, not proof.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

# Order matters: every Chromium browser also says "Chrome", and almost
# everything says "Safari", so the specific names have to be tried first.
BROWSERS: list[tuple[str, re.Pattern[str]]] = [
    ("Edge", re.compile(r"Edg(?:e|A|iOS)?/")),
    ("Samsung Internet", re.compile(r"SamsungBrowser/")),
    ("Yandex", re.compile(r"YaBrowser/")),
    ("Opera", re.compile(r"OPR/|OPiOS/|Opera")),
    ("Firefox", re.compile(r"Firefox/|FxiOS/")),
    ("Chrome", re.compile(r"Chrome/|CriOS/|CrMo/")),
    ("Safari", re.compile(r"Safari/")),
]

SYSTEMS: list[tuple[str, re.Pattern[str]]] = [
    ("iPadOS", re.compile(r"iPad")),
    ("iOS", re.compile(r"iPhone|iPod")),
    ("Android", re.compile(r"Android")),
    ("Windows", re.compile(r"Windows NT|Windows Phone")),
    ("macOS", re.compile(r"Macintosh|Mac OS X")),
    ("Linux", re.compile(r"X11|Linux")),
]

PHONE = "phone"
TABLET = "tablet"
COMPUTER = "computer"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Device:
    """What could be read off a user agent, and a stable id for that shape."""

    browser: str
    system: str
    kind: str
    fingerprint: str

    @property
    def is_recognised(self) -> bool:
        return bool(self.browser or self.system)

    def label(self) -> str:
        """A short human name, for a notification body or an email line."""
        parts = [part for part in (self.browser, self.system) if part]
        return " · ".join(parts)


def _match(candidates: list[tuple[str, re.Pattern[str]]], user_agent: str) -> str:
    for name, pattern in candidates:
        if pattern.search(user_agent):
            return name
    return ""


def _kind(user_agent: str, system: str) -> str:
    if system == "iPadOS":
        return TABLET
    if system == "iOS":
        return PHONE
    if system == "Android":
        # Android puts "Mobile" in the string for phones and leaves it out for
        # tablets, which is the only signal the string carries about size.
        return PHONE if "Mobile" in user_agent else TABLET
    if system in {"Windows", "macOS", "Linux"}:
        return COMPUTER
    return UNKNOWN


def describe(user_agent: str) -> Device:
    """Read a user agent into a device shape, with a fingerprint for that shape.

    An unreadable or absent user agent still gets a fingerprint, so a client
    that sends nothing is one device rather than a new one on every sign-in.
    Note that an iPad running Safari asks for desktop pages by default and
    identifies itself as a Mac, so an iPad often arrives here as macOS; it is
    consistent about it, which is what matters for telling devices apart.
    """
    user_agent = (user_agent or "").strip()
    browser = _match(BROWSERS, user_agent)
    system = _match(SYSTEMS, user_agent)
    kind = _kind(user_agent, system)

    # The fingerprint is built from the three fields above and nothing else, so
    # it survives an update and changes when the device really does.
    shape = f"{browser}|{system}|{kind}".lower()
    fingerprint = hashlib.sha256(shape.encode("utf-8")).hexdigest()

    return Device(browser=browser, system=system, kind=kind, fingerprint=fingerprint)
