"""Rendering a CV document to a PDF file.

A CV is only worth assembling if it can leave the platform: an application form
asks for a file, not for a URL behind a login. So this renders the very payload
``build_cv_payload`` already produces — the file cannot disagree with what the
screen shows, because both read the same assembly.

The layout follows the conventions the project was asked to adopt, and each of
them is a decision rather than a default:

* **One column.** The obvious temptation is the two-column CV with a tinted
  sidebar. Applicant tracking systems read a PDF as a stream of text, and a
  sidebar interleaves two unrelated columns into that stream — the screen that
  decides whether a person is read at all sees nonsense. A plain single column
  survives the machine and loses nothing to the human.
* **Margins 20mm, 25mm on the left.** The binding edge is wider because a CV
  gets printed and filed.
* **One typeface, a short scale.** Hierarchy comes from weight and case, not
  from a second family or a spread of sizes.
* **Reverse chronological order**, applied here rather than trusted from the
  query: the most recent post is the one being hired against.
* **Education before experience**, which is the order ``DEFAULT_SECTIONS``
  already sets, and the right one for the students this platform is for.
* **No photograph.** Most markets do not ask for one, several discourage it,
  and it is one more thing for a parser to trip over.
* **Levels in words.** "B2" is a certificate; "upper-intermediate" is a fact a
  reader can use without looking up the scale.
"""

from __future__ import annotations

import datetime as dt
import logging
from io import BytesIO
from typing import Any, Callable

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from .typography import FONT_FAMILY, register_fonts, sanitise

# fpdf2 subsets the embedded font on every single render and narrates it at
# INFO. That is one request's worth of noise per download in the production
# log, saying nothing anyone will ever read.
logging.getLogger("fontTools.subset").setLevel(logging.WARNING)

# Page geometry in millimetres, A4.
PAGE_WIDTH = 210.0
PAGE_HEIGHT = 297.0
MARGIN_TOP = 20.0
MARGIN_BOTTOM = 20.0
MARGIN_LEFT = 25.0
MARGIN_RIGHT = 20.0
CONTENT_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT

#: Width reserved at the right edge of an entry line for its dates, so every
#: period on the page starts at the same x and the column can be read down.
DATE_WIDTH = 42.0

#: The skills column carries more than a date does — a score over its
#: denominator and, when earned, the word that says it was verified.
SKILL_WIDTH = 54.0

#: The top of the proficiency scale, from ProficiencyBand's 0-100.
SKILL_SCALE_MAX = 100

# Print colours. The page is white paper, so the Aurora Glass tokens do not
# apply: ink is near-black rather than black, which is as harsh on paper as it
# is on a screen, and the one accent is the platform's deep violet — dark
# enough to carry the gravity a formal document wants, and still the project's
# own colour rather than the stock navy of every CV template.
INK = (22, 20, 32)
MUTED = (92, 87, 108)
RULE = (205, 200, 216)
ACCENT = (48, 35, 87)

BODY_SIZE = 9.5
NOTE_SIZE = 8.5
LINE = 4.8

#: Section captions and the words set around them. The backend carries no
#: gettext catalogues, so these live here rather than pretending otherwise.
LABELS: dict[str, dict[str, str]] = {
    "uz": {
        "document": "Rezyume",
        "summary": "Qisqacha ma’lumot",
        "education": "Ta’lim",
        "skills": "Ko‘nikmalar",
        "experience": "Ish tajribasi",
        "projects": "Loyihalar",
        "courses": "Kurslar",
        "certificates": "Sertifikatlar",
        "languages": "Tillar",
        "present": "hozirgacha",
        "verified": "tasdiqlangan",
        "youth_id": "Youth ID",
        "page": "{current}-bet, jami {total}",
        "compiled": "Tuzilgan sana: {date}",
    },
    "ru": {
        "document": "Резюме",
        "summary": "О себе",
        "education": "Образование",
        "skills": "Навыки",
        "experience": "Опыт работы",
        "projects": "Проекты",
        "courses": "Курсы",
        "certificates": "Сертификаты",
        "languages": "Языки",
        "present": "настоящее время",
        "verified": "подтверждён",
        "youth_id": "Youth ID",
        "page": "стр. {current} из {total}",
        "compiled": "Составлено {date}",
    },
    "en": {
        "document": "Curriculum vitae",
        "summary": "Summary",
        "education": "Education",
        "skills": "Skills",
        "experience": "Experience",
        "projects": "Projects",
        "courses": "Courses",
        "certificates": "Certificates",
        "languages": "Languages",
        "present": "present",
        "verified": "verified",
        "youth_id": "Youth ID",
        "page": "page {current} of {total}",
        "compiled": "Prepared {date}",
    },
}

#: Drawn in the header rather than as a section of their own, because somebody
#: looking for an address looks at the top of the page.
HEADER_SECTIONS = frozenset({"contacts"})

#: A proficiency band written out. A reader who does not hold the CEFR scale in
#: their head learns nothing from "B2", and the advice this layout follows is
#: explicit that levels belong in words.
LANGUAGE_LEVELS: dict[str, dict[str, str]] = {
    "uz": {
        "native": "ona tili",
        "c2": "mukammal",
        "c1": "erkin",
        "b2": "o‘rtadan yuqori",
        "b1": "o‘rta",
        "a2": "boshlang‘ich",
        "a1": "boshlovchi",
    },
    "ru": {
        "native": "родной",
        "c2": "в совершенстве",
        "c1": "свободно",
        "b2": "выше среднего",
        "b1": "средний",
        "a2": "базовый",
        "a1": "начальный",
    },
    "en": {
        "native": "native",
        "c2": "proficient",
        "c1": "advanced",
        "b2": "upper-intermediate",
        "b1": "intermediate",
        "a2": "elementary",
        "a1": "beginner",
    },
}

#: The three the platform itself speaks, named in each of them. Anything else
#: is printed as the code gave it, which is better than guessing wrongly.
LANGUAGE_NAMES: dict[str, dict[str, str]] = {
    "uz": {"uz": "O‘zbek", "ru": "Rus", "en": "Ingliz"},
    "ru": {"uz": "Узбекский", "ru": "Русский", "en": "Английский"},
    "en": {"uz": "Uzbek", "ru": "Russian", "en": "English"},
}


class _CVPdf(FPDF):
    """An A4 document that signs every page.

    A printed CV is routinely separated from its first sheet, and page two of a
    stack of applications is anonymous unless it says whose it is. So the
    footer repeats the name and numbers the page *out of* the total — which
    also tells a reader holding one sheet that another one exists.
    """

    def __init__(self, *, owner: str, labels: dict[str, str]) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._owner = owner
        self._labels = labels
        self.set_margins(MARGIN_LEFT, MARGIN_TOP, MARGIN_RIGHT)
        self.set_auto_page_break(auto=True, margin=MARGIN_BOTTOM)
        self.alias_nb_pages()
        register_fonts(self)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_font(FONT_FAMILY, "", 7.5)
        self.set_text_color(*MUTED)
        self.cell(CONTENT_WIDTH / 2, 5, text=sanitise(self._owner))
        # `{nb}` is substituted with the final page count when the document is
        # closed, so the total can be printed before it is known.
        self.cell(
            CONTENT_WIDTH / 2,
            5,
            text=self._labels["page"].format(current=self.page_no(), total="{nb}"),
            align="R",
        )


def _labels_for(language: str | None) -> tuple[str, dict[str, str]]:
    code = (language or "").lower()
    return (code if code in LABELS else "ru"), LABELS.get(code, LABELS["ru"])


def _as_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _month(value: Any) -> str:
    """A date as ``MM.YYYY`` — the precision a CV period actually carries."""
    date = _as_date(value)
    return f"{date.month:02d}.{date.year}" if date else ""


def _year(value: Any) -> str:
    """Education and certificates are dated by year; the month is noise."""
    date = _as_date(value)
    return str(date.year) if date else ""


def _period(entry: dict, labels: dict[str, str], *, years_only: bool = False) -> str:
    render = _year if years_only else _month
    start = render(entry.get("start_date"))
    end = labels["present"] if entry.get("is_current") else render(entry.get("end_date"))
    if start and end and start != end:
        return f"{start} – {end}"
    return end or start


def _recent_first(entries: list[dict]) -> list[dict]:
    """Reverse chronological, with anything still running at the top.

    Sorted here rather than in the query because the payload gathers several
    kinds of record and only the document knows they are being read as a
    history. An undated entry sorts last: it cannot claim recency it has not
    shown.
    """
    floor = dt.date.min

    def key(entry: dict) -> tuple[int, dt.date, dt.date]:
        start = _as_date(entry.get("start_date")) or floor
        end = _as_date(entry.get("end_date")) or floor
        return (1 if entry.get("is_current") else 0, end, start)

    return sorted(entries, key=key, reverse=True)


def _by_date(entries: list[dict], field: str) -> list[dict]:
    return sorted(entries, key=lambda e: _as_date(e.get(field)) or dt.date.min, reverse=True)


class _Writer:
    """The drawing primitives, so each section below reads as its own content."""

    def __init__(self, pdf: _CVPdf, labels: dict[str, str], code: str) -> None:
        self.pdf = pdf
        self.labels = labels
        self.code = code

    # -- structure ---------------------------------------------------------

    def heading(self, key: str) -> None:
        pdf = self.pdf
        # Keep a caption with at least a line of what it introduces: a heading
        # stranded at the foot of a page is a promise the page does not keep.
        if pdf.get_y() > PAGE_HEIGHT - MARGIN_BOTTOM - 28:
            pdf.add_page()
        pdf.ln(4.5)
        pdf.set_font(FONT_FAMILY, "B", 9)
        pdf.set_text_color(*ACCENT)
        pdf.cell(
            CONTENT_WIDTH,
            5,
            text=sanitise(self.labels.get(key, key)).upper(),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.set_draw_color(*RULE)
        pdf.set_line_width(0.2)
        y = pdf.get_y() + 0.4
        pdf.line(MARGIN_LEFT, y, MARGIN_LEFT + CONTENT_WIDTH, y)
        pdf.ln(2.6)

    def entry(self, title: str, period: str = "") -> None:
        """The first line of a record: what it was, and when."""
        pdf = self.pdf
        width = CONTENT_WIDTH - (DATE_WIDTH if period else 0)
        pdf.set_font(FONT_FAMILY, "B", 10)
        pdf.set_text_color(*INK)
        pdf.cell(width, 5.2, text=sanitise(title))
        if period:
            pdf.set_font(FONT_FAMILY, "", NOTE_SIZE)
            pdf.set_text_color(*MUTED)
            pdf.cell(DATE_WIDTH, 5.2, text=sanitise(period), align="R")
        pdf.ln(5.2)

    def row(self, left: str, right: str = "", *, right_width: float = DATE_WIDTH) -> None:
        """A plain two-part line, for skills and languages."""
        pdf = self.pdf
        width = CONTENT_WIDTH - (right_width if right else 0)
        pdf.set_font(FONT_FAMILY, "", BODY_SIZE)
        pdf.set_text_color(*INK)
        pdf.cell(width, LINE, text=sanitise(left))
        if right:
            pdf.set_font(FONT_FAMILY, "", NOTE_SIZE)
            pdf.set_text_color(*MUTED)
            pdf.cell(right_width, LINE, text=sanitise(right), align="R")
        pdf.ln(LINE)

    def note(self, text: str) -> None:
        """A secondary line — an employer, a provider, a list of skills."""
        if not text:
            return
        pdf = self.pdf
        pdf.set_font(FONT_FAMILY, "", NOTE_SIZE)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(
            CONTENT_WIDTH,
            4.3,
            text=sanitise(text),
            align="L",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    def body(self, text: str) -> None:
        """A paragraph, set ragged right.

        fpdf2 justifies by default, and on a 165mm measure that opens rivers of
        white through four lines of prose. Even word spacing skims better, and
        a CV is skimmed.
        """
        if not text:
            return
        pdf = self.pdf
        pdf.set_font(FONT_FAMILY, "", BODY_SIZE)
        pdf.set_text_color(*INK)
        pdf.multi_cell(
            CONTENT_WIDTH,
            LINE,
            text=sanitise(text),
            align="L",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    def duties(self, text: str) -> None:
        """What was done, as a list when the author wrote one.

        Responsibilities belong in bullets — but only the author knows where
        one ends. A description written as a single paragraph is set as a
        paragraph rather than chopped at full stops into sentences pretending
        to be achievements.
        """
        lines = [line.strip(" •-–—\t") for line in (text or "").splitlines()]
        lines = [line for line in lines if line]
        if len(lines) < 2:
            self.body(text)
            return
        pdf = self.pdf
        for line in lines:
            pdf.set_font(FONT_FAMILY, "", BODY_SIZE)
            pdf.set_text_color(*INK)
            pdf.cell(4, LINE, text="—")
            pdf.multi_cell(
                CONTENT_WIDTH - 4,
                LINE,
                text=sanitise(line),
                align="L",
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )

    def gap(self, height: float = 2.2) -> None:
        self.pdf.ln(height)


def _render_header(writer: _Writer, payload: dict) -> None:
    pdf, labels = writer.pdf, writer.labels
    personal = payload.get("personal") or {}
    contacts = payload.get("contacts") or {}

    pdf.set_font(FONT_FAMILY, "B", 7.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(
        CONTENT_WIDTH,
        4,
        text=sanitise(labels["document"]).upper(),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )

    name = personal.get("full_name") or (payload.get("meta") or {}).get("title") or ""
    pdf.set_font(FONT_FAMILY, "B", 20)
    pdf.set_text_color(*INK)
    pdf.multi_cell(
        CONTENT_WIDTH, 8.6, text=sanitise(name), new_x=XPos.LMARGIN, new_y=YPos.NEXT
    )

    # The role, in the wording of the posting being answered — the single line
    # that tells a reader which pile this belongs in.
    if personal.get("headline"):
        pdf.set_font(FONT_FAMILY, "", 11)
        pdf.set_text_color(*ACCENT)
        pdf.multi_cell(
            CONTENT_WIDTH,
            5.4,
            text=sanitise(personal["headline"]),
            align="L",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    # Everything needed to reach or identify the person, on one line. Absent
    # fields drop out rather than leaving stranded separators.
    place = ", ".join(p for p in (personal.get("city"), personal.get("region")) if p)
    youth_id = personal.get("youth_id")
    parts = [
        contacts.get("email") or "",
        contacts.get("phone") or "",
        place,
        f"{labels['youth_id']} {youth_id}" if youth_id else "",
    ]
    line = "  ·  ".join(p for p in parts if p)
    if line:
        pdf.ln(0.8)
        pdf.set_font(FONT_FAMILY, "", NOTE_SIZE)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(
            CONTENT_WIDTH, 4.3, text=sanitise(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )

    pdf.ln(1.6)
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.6)
    y = pdf.get_y()
    pdf.line(MARGIN_LEFT, y, MARGIN_LEFT + CONTENT_WIDTH, y)


def _render_summary(writer: _Writer, payload: dict) -> None:
    writer.body(payload.get("summary") or "")


def _render_education(writer: _Writer, payload: dict) -> None:
    for entry in _recent_first(payload.get("education") or []):
        degree = " — ".join(
            p for p in (entry.get("degree"), entry.get("field_of_study")) if p
        )
        heading = degree or entry.get("institution") or ""
        writer.entry(heading, _period(entry, writer.labels, years_only=True))
        if degree and entry.get("institution"):
            writer.note(entry["institution"])
        writer.gap()


def skill_annotation(skill: dict, labels: dict[str, str]) -> str:
    """What is printed to the right of a skill.

    The score is kept — it is the thing this platform exists to produce, and a
    CV that drops it throws away its own evidence. But it is printed over its
    denominator rather than alone: "65" means nothing to a reader who has never
    seen this scale, while "65 / 100" means something to everyone, and no key
    has to be printed anywhere for it. The verified mark stays beside it,
    because that is the half an employer cannot get from a candidate's own
    account of themselves.
    """
    score = skill.get("proficiency")
    parts = [] if score is None else [f"{score} / {SKILL_SCALE_MAX}"]
    if skill.get("verified"):
        parts.append(labels["verified"])
    return "  ·  ".join(parts)


def _render_skills(writer: _Writer, payload: dict) -> None:
    for skill in payload.get("skills") or []:
        writer.row(
            skill.get("name") or "",
            skill_annotation(skill, writer.labels),
            right_width=SKILL_WIDTH,
        )


def _render_experience(writer: _Writer, payload: dict) -> None:
    for entry in _recent_first(payload.get("experience") or []):
        writer.entry(entry.get("title") or "", _period(entry, writer.labels))
        if entry.get("organization"):
            writer.note(entry["organization"])
        writer.duties(entry.get("description") or "")
        if entry.get("skills"):
            writer.note(", ".join(entry["skills"]))
        writer.gap()


def _render_projects(writer: _Writer, payload: dict) -> None:
    for item in payload.get("projects") or []:
        writer.entry(item.get("title") or "")
        writer.duties(item.get("description") or "")
        if item.get("skills"):
            writer.note(", ".join(item["skills"]))
        writer.note(item.get("url") or "")
        writer.gap()


def _render_courses(writer: _Writer, payload: dict) -> None:
    for course in _by_date(payload.get("courses") or [], "completed_at"):
        writer.entry(course.get("title") or "", _year(course.get("completed_at")))
        writer.note(course.get("provider") or "")
        writer.gap(1.2)


def _render_certificates(writer: _Writer, payload: dict) -> None:
    for cert in _by_date(payload.get("certificates") or [], "issued_at"):
        writer.entry(cert.get("title") or "", _year(cert.get("issued_at")))
        writer.note(cert.get("serial") or "")
        writer.gap(1.2)


def _render_languages(writer: _Writer, payload: dict) -> None:
    names = LANGUAGE_NAMES.get(writer.code, LANGUAGE_NAMES["ru"])
    levels = LANGUAGE_LEVELS.get(writer.code, LANGUAGE_LEVELS["ru"])
    for entry in payload.get("languages") or []:
        if not isinstance(entry, dict):
            writer.row(str(entry))
            continue
        code = (entry.get("code") or "").lower()
        level = (entry.get("level") or "").lower()
        writer.row(names.get(code, code.upper()), levels.get(level, entry.get("level") or ""))


RENDERERS: dict[str, Callable[[_Writer, dict], None]] = {
    "summary": _render_summary,
    "education": _render_education,
    "skills": _render_skills,
    "experience": _render_experience,
    "projects": _render_projects,
    "courses": _render_courses,
    "certificates": _render_certificates,
    "languages": _render_languages,
}

#: Characters a filename cannot carry on Windows, which is where most of these
#: files will land. Separators go too: a name is one token in a folder.
_UNSAFE_IN_FILENAME = set('/\\:*?"<>|') | {chr(c) for c in range(32)}


def suggest_filename(payload: dict) -> str:
    """What the browser should call the downloaded file.

    Built from the person's name rather than the document title, because the
    file is read by somebody who has a folder of them: "Azizaxon-Ibragimova.pdf"
    is findable in a way that "My-CV.pdf" is not. Case and Cyrillic are kept —
    Django encodes the header per RFC 5987 — and only what a filesystem would
    actually reject is removed.
    """
    meta = payload.get("meta") or {}
    source = (payload.get("personal") or {}).get("full_name") or meta.get("title") or ""
    cleaned = "".join(" " if ch in _UNSAFE_IN_FILENAME else ch for ch in source)
    stem = "-".join(cleaned.split()) or "CV"
    return f"{stem[:80]}.pdf"


def _has_content(key: str, payload: dict) -> bool:
    value = payload.get(key)
    if key == "summary":
        return bool((value or "").strip())
    return bool(value)


def render_cv_pdf(
    payload: dict, *, language: str | None = None, today: dt.date | None = None
) -> bytes:
    """Render an assembled CV payload to PDF bytes.

    Empty sections are skipped rather than printed as a caption over nothing: a
    CV showing a bare "Experience" heading says less than one without it.
    """
    meta = payload.get("meta") or {}
    code, labels = _labels_for(language or meta.get("language"))
    owner = (payload.get("personal") or {}).get("full_name") or meta.get("title") or ""

    pdf = _CVPdf(owner=owner, labels=labels)
    pdf.set_title(meta.get("title") or labels["document"])
    pdf.set_author(owner)
    pdf.add_page()

    writer = _Writer(pdf, labels, code)
    _render_header(writer, payload)

    for key in meta.get("sections") or []:
        if key in HEADER_SECTIONS or key not in RENDERERS:
            continue
        if not _has_content(key, payload):
            continue
        writer.heading(key)
        RENDERERS[key](writer, payload)

    # The date the document was prepared. A CV without one is undated evidence:
    # the reader cannot tell whether "present" means this month or two years
    # ago, and the convention this layout follows asks for it explicitly.
    stamp = today or dt.date.today()
    pdf.ln(6)
    pdf.set_font(FONT_FAMILY, "", 7.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(
        CONTENT_WIDTH,
        4,
        text=sanitise(labels["compiled"].format(date=stamp.strftime("%d.%m.%Y"))),
        align="R",
    )

    out = BytesIO()
    pdf.output(out)
    return out.getvalue()
