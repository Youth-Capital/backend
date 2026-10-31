"""The typeface a generated PDF is set in.

The fourteen fonts every PDF reader is guaranteed to own are all Latin-1, so
Cyrillic set in Helvetica arrives as a row of empty boxes — and Russian is this
platform's second language. The file therefore has to carry its own font.

``assets/fonts`` holds two static instances cut from Manrope's variable
original with fontTools, at the two weights a document actually uses. They are
committed rather than fetched at build time because the image build has no
network, and Manrope is the platform's own typeface, so a printed CV and the
screen it was assembled on are set in the same letters.
"""

from __future__ import annotations

from pathlib import Path

from fpdf import FPDF

FONT_FAMILY = "Manrope"
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

#: Characters the cut instances have no glyph for, mapped to the nearest one
#: they do. A CV carries text its owner typed, so this is not hypothetical:
#: Uzbek Latin is written with U+02BB/U+02BC on some keyboards and with the
#: typographic quotes on others, and only the second pair is in the font. A
#: missing glyph is not an error in PDF — it simply prints as nothing, which
#: would silently drop the apostrophe out of "Oʻzbekiston".
SUBSTITUTIONS = {
    "ʻ": "‘",  # modifier letter turned comma
    "ʼ": "’",  # modifier letter apostrophe
    "ʾ": "‘",
    "ʿ": "‘",
    "′": "’",  # prime, pasted from spreadsheets
    " ": " ",  # non-breaking space, pasted from Word
    "‑": "‐",  # non-breaking hyphen
    "−": "–",  # minus sign used as a dash
}

_TRANSLATION = str.maketrans(SUBSTITUTIONS)


def register_fonts(pdf: FPDF) -> None:
    """Make ``FONT_FAMILY`` available on this document in regular and bold."""
    pdf.add_font(FONT_FAMILY, "", FONT_DIR / "Manrope-Regular.ttf")
    pdf.add_font(FONT_FAMILY, "B", FONT_DIR / "Manrope-Bold.ttf")


def sanitise(text: str | None) -> str:
    """Text the bundled font can actually draw.

    Applied at every call site rather than once at the top, because the payload
    is a nested structure of user-authored strings and a single missed branch
    is a word that quietly loses its punctuation.
    """
    if not text:
        return ""
    return str(text).translate(_TRANSLATION)
