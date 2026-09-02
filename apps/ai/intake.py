"""The intake interview a new learner answers before seeing a dashboard.

Why an engine and not just a chat prompt
----------------------------------------
The *schema* is fixed by code, not by the model. Everything downstream —
matching, the capital index, the development plan — needs specific fields to
exist with specific types. An LLM free-forming its own structure would break
all three, silently.

So the split is:

* this module decides **what must be learned** and in what order;
* the AI provider decides **how it is asked** — rephrasing, probing a vague
  answer, reacting to what the person just said.

With the rule-based provider (no API key, the default) that second part is
absent and the result is an adaptive questionnaire: still branching, still
skipping what does not apply, just not conversational. With an LLM configured
the same question set becomes a real conversation. Nothing downstream changes
either way, which is the point.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable

# --------------------------------------------------------------------------
# Question shapes
# --------------------------------------------------------------------------
TEXT = "TEXT"
SINGLE = "SINGLE"
MULTI = "MULTI"
SKILLS = "SKILLS"
SCALE = "SCALE"


@dataclass(frozen=True)
class Choice:
    value: str
    label_key: str


@dataclass(frozen=True)
class Question:
    """One thing to learn.

    `label_key` is an i18n key rather than a sentence: the interview runs in
    Uzbek, Russian and English, and a question bank written in one of them
    would quietly become the source of truth for the other two.
    """

    id: str
    kind: str
    label_key: str
    hint_key: str = ""
    choices: tuple[Choice, ...] = ()
    required: bool = True
    #: Receives all answers so far; return False to skip this question.
    applies: Callable[[dict], bool] | None = None
    #: The question whose answer decides `applies`. Needed for honest progress:
    #: before it is answered the branch is *undecided*, not excluded.
    depends_on: str = ""
    max_choices: int = 0
    placeholder_key: str = ""

    def is_applicable(self, answers: dict) -> bool:
        return self.applies is None or self.applies(answers)


def _c(*pairs: tuple[str, str]) -> tuple[Choice, ...]:
    return tuple(Choice(value, key) for value, key in pairs)


# --------------------------------------------------------------------------
# The bank
#
# Order matters: each answer narrows the ones after it. "Tell me about
# yourself" comes first on purpose — an open question is a gentler opening
# than a dropdown, and its text is also the raw material for the bio.
# --------------------------------------------------------------------------
def _is_studying(answers: dict) -> bool:
    return answers.get("education_status") in {"SCHOOL", "COLLEGE", "UNIVERSITY"}


def _is_post_school(answers: dict) -> bool:
    return answers.get("education_status") in {"COLLEGE", "UNIVERSITY", "GRADUATE"}


def _knows_target(answers: dict) -> bool:
    return answers.get("knows_profession") == "YES"


QUESTIONS: tuple[Question, ...] = (
    Question(
        id="about",
        kind=TEXT,
        label_key="intake.q.about",
        hint_key="intake.q.aboutHint",
        placeholder_key="intake.q.aboutPlaceholder",
        required=False,
    ),
    Question(
        id="education_status",
        kind=SINGLE,
        label_key="intake.q.educationStatus",
        choices=_c(
            ("SCHOOL", "intake.opt.school"),
            ("COLLEGE", "intake.opt.college"),
            ("UNIVERSITY", "intake.opt.university"),
            ("GRADUATE", "intake.opt.graduate"),
            ("NONE", "intake.opt.notStudying"),
        ),
    ),
    Question(
        id="institution",
        depends_on="education_status",
        kind=TEXT,
        label_key="intake.q.institution",
        placeholder_key="intake.q.institutionPlaceholder",
        required=False,
        applies=_is_studying,
    ),
    Question(
        id="field_of_study",
        depends_on="education_status",
        kind=TEXT,
        label_key="intake.q.fieldOfStudy",
        placeholder_key="intake.q.fieldPlaceholder",
        required=False,
        applies=_is_post_school,
    ),
    Question(
        id="goals",
        kind=MULTI,
        label_key="intake.q.goals",
        hint_key="intake.q.goalsHint",
        choices=_c(
            ("CHOOSE_CAREER", "intake.opt.chooseCareer"),
            ("LEARN_NEW", "intake.opt.learnNew"),
            ("IMPROVE", "intake.opt.improve"),
            ("INTERNSHIP", "intake.opt.internship"),
            ("FIND_JOB", "intake.opt.findJob"),
            ("CHANGE_CAREER", "intake.opt.changeCareer"),
            ("BUILD_CV", "intake.opt.buildCv"),
            ("EXPERIENCE", "intake.opt.gainExperience"),
        ),
    ),
    Question(
        id="interests",
        kind=MULTI,
        label_key="intake.q.interests",
        hint_key="intake.q.interestsHint",
        # Values are SkillCategory slugs, so an answer maps straight onto the
        # taxonomy the recommender already reads.
        choices=_c(
            ("technology", "intake.opt.technology"),
            ("programming", "intake.opt.programming"),
            ("data", "intake.opt.data"),
            ("cybersecurity", "intake.opt.cybersecurity"),
            ("design", "intake.opt.design"),
            ("business", "intake.opt.business"),
            ("marketing", "intake.opt.marketing"),
            ("finance", "intake.opt.finance"),
            ("languages", "intake.opt.languages"),
            ("civic", "intake.opt.civic"),
        ),
    ),
    Question(
        id="knows_profession",
        kind=SINGLE,
        label_key="intake.q.knowsProfession",
        choices=_c(
            ("YES", "intake.opt.knowsYes"),
            ("NO", "intake.opt.knowsNo"),
        ),
    ),
    Question(
        id="target_profession",
        depends_on="knows_profession",
        kind=SINGLE,
        label_key="intake.q.targetProfession",
        hint_key="intake.q.targetProfessionHint",
        # Choices are filled from the professions table at serialisation time.
        applies=_knows_target,
        required=False,
    ),
    Question(
        id="skills",
        kind=SKILLS,
        label_key="intake.q.skills",
        hint_key="intake.q.skillsHint",
        required=False,
    ),
    Question(
        id="experience_kinds",
        kind=MULTI,
        label_key="intake.q.experience",
        hint_key="intake.q.experienceHint",
        required=False,
        choices=_c(
            ("JOB", "intake.opt.job"),
            ("INTERNSHIP", "intake.opt.internshipExp"),
            ("PROJECT", "intake.opt.project"),
            ("FREELANCE", "intake.opt.freelance"),
            ("VOLUNTEER", "intake.opt.volunteer"),
            ("HACKATHON", "intake.opt.hackathon"),
            ("COMPETITION", "intake.opt.competition"),
            ("CERTIFICATE", "intake.opt.certificate"),
            ("NONE", "intake.opt.noExperience"),
        ),
    ),
    Question(
        id="hours_per_week",
        kind=SINGLE,
        label_key="intake.q.hours",
        hint_key="intake.q.hoursHint",
        choices=_c(
            ("LT_3", "intake.opt.hoursLt3"),
            ("3_7", "intake.opt.hours3to7"),
            ("8_15", "intake.opt.hours8to15"),
            ("GT_15", "intake.opt.hoursGt15"),
        ),
    ),
)

BY_ID = {question.id: question for question in QUESTIONS}


# --------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------
@dataclass
class InterviewState:
    answers: dict[str, Any] = field(default_factory=dict)

    def applicable(self) -> list[Question]:
        """Questions that apply given what is known right now."""
        return [q for q in QUESTIONS if q.is_applicable(self.answers)]

    def projected(self) -> list[Question]:
        """Questions that still *might* be asked.

        A branch whose gate is unanswered counts as undecided and stays in.
        Excluding it would make the total grow the moment the gate is answered
        — the interview would open at "0 of 8" and become "2 of 10", so the
        progress bar would visibly move backwards.
        """
        out = []
        for question in QUESTIONS:
            if question.applies is None:
                out.append(question)
            elif question.depends_on and question.depends_on not in self.answers:
                out.append(question)
            elif question.is_applicable(self.answers):
                out.append(question)
        return out

    def next_question(self) -> Question | None:
        for question in self.applicable():
            if question.id not in self.answers:
                return question
        return None

    def progress(self) -> tuple[int, int]:
        """Answered / total. The total may shrink as branches close, never grow."""
        projected = self.projected()
        answered = sum(1 for q in projected if q.id in self.answers)
        return answered, len(projected)

    def is_complete(self) -> bool:
        return self.next_question() is None


def validate_answer(question: Question, value: Any) -> Any:
    """Coerce and check one answer. Raises ValueError with a stable code."""
    if question.kind == TEXT:
        text = "" if value is None else str(value).strip()
        if question.required and not text:
            raise ValueError("answer_required")
        return text[:2000]

    if question.kind == SINGLE:
        if value in (None, ""):
            if question.required:
                raise ValueError("answer_required")
            return ""
        allowed = {choice.value for choice in question.choices}
        # target_profession's choices come from the database, so an empty
        # `choices` tuple means "anything the caller sends", checked later.
        if allowed and str(value) not in allowed:
            raise ValueError("invalid_choice")
        return str(value)

    if question.kind == MULTI:
        if not isinstance(value, list):
            raise ValueError("expected_list")
        allowed = {choice.value for choice in question.choices}
        cleaned = [str(item) for item in value if str(item) in allowed]
        if question.required and not cleaned:
            raise ValueError("answer_required")
        if question.max_choices:
            cleaned = cleaned[: question.max_choices]
        return cleaned

    if question.kind == SKILLS:
        if not isinstance(value, list):
            raise ValueError("expected_list")
        cleaned = []
        for item in value:
            if not isinstance(item, dict) or not item.get("skill"):
                continue
            try:
                level = int(item.get("level", 40))
            except (TypeError, ValueError):
                level = 40
            cleaned.append(
                {"skill": str(item["skill"]), "level": max(0, min(100, level))}
            )
        return cleaned[:30]

    if question.kind == SCALE:
        try:
            return max(0, min(100, int(value)))
        except (TypeError, ValueError):
            raise ValueError("expected_number")

    raise ValueError("unknown_question_kind")


def question_payload(question: Question, *, profession_choices=None) -> dict:
    """Serialise one question for the client.

    Profession choices are injected rather than hardcoded so the interview
    always offers exactly what the taxonomy currently holds.
    """
    payload = asdict(question)
    payload.pop("applies", None)
    payload["choices"] = [asdict(choice) for choice in question.choices]

    if question.id == "target_profession" and profession_choices:
        payload["choices"] = [
            {"value": str(profession.id), "label_key": "", "label": profession.name}
            for profession in profession_choices
        ]
    return payload
