"""What the deterministic safety screen must catch, and must not.

This layer runs before the model is asked anything, and the module's own
docstring is the reason it has to be tested directly: it is "the layer we
control and can prove the behaviour of". A screen that is only checked through
the model proves nothing — the model may cover for it, which is exactly what
was happening here.

Two failure directions, and both are real damage:

* **A miss** sends a fifteen-year-old saying "у меня депрессия" into a careers
  assistant instead of towards a specialist. The platform serves minors, so
  this is the case the layer exists for.
* **A false positive** refuses an ordinary product question. "Меня тревожит
  собеседование" is nerves before an interview — the single most normal thing
  a user of this platform will ever type — and it used to be escalated as a
  medical matter.

The words are listed per language on purpose. Uzbek, Russian and English do
not share stems, and the bug this file was written for was precisely a stem
that covered English and left Russian open.
"""

import pytest

from apps.ai.safety import SafetyAction, check_text

pytestmark = pytest.mark.django_db


def verdict(text):
    return check_text(text)


# -- the cases that must reach a human ------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        # Russian — the half that was missing entirely.
        "У меня депрессия из-за учёбы",
        "Кажется, у меня депрессию давно не лечили",
        "У меня суицидальные мысли",
        "Думаю о самоубийстве",
        "У меня паническая атака перед экзаменом",
        "У меня тревожное расстройство",
        "У меня тревожность уже полгода",
        # Uzbek.
        "Menda ruhiy tushkunlik bor",
        "O'z joniga qasd qilish haqida o'ylayapman",
        "Menda vahima xuruji bo'ldi",
        # English.
        "I think I have depression",
        "I have suicidal thoughts",
        "I get panic attacks before exams",
        # Medication and diagnosis, in every language.
        "Какие лекарства помогают сосредоточиться?",
        "Menga qanday dori kerak?",
        "Can you give me a prescription?",
    ],
)
def test_a_medical_matter_goes_to_a_specialist(text):
    result = verdict(text)

    assert not result.allowed, f"reached the model unscreened: {text}"
    assert result.action == SafetyAction.ESCALATED
    assert result.escalate_to == "medical"


# -- the cases that must NOT be refused -------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        # The verb, not the diagnosis: nerves before an interview.
        "Меня тревожит завтрашнее собеседование, что делать?",
        "Меня тревожит, что у меня мало опыта",
        "Тревожно перед защитой диплома",
        # Ordinary career and study questions that sit near the vocabulary.
        "Как подготовиться к техническому собеседованию?",
        "Стоит ли идти в аналитику или в разработку?",
        "Как договориться о зарплате?",
        "Сколько времени займёт курс по SQL?",
        "Menga qanday kurs mos keladi?",
        "What skills do I need for a backend role?",
    ],
)
def test_an_ordinary_question_is_not_refused(text):
    assert verdict(text).allowed, (
        f"refused a normal question: {text} — a screen that blocks the product's "
        "own subject matter is worse than no screen"
    )


# -- the shape of the guarantee ---------------------------------------------
def test_the_screen_runs_before_the_model_is_asked():
    """Escalation is not advice: the refused text carries a handoff, not an answer."""
    result = verdict("У меня депрессия")

    assert result.message == "ai.safety.escalate"
    assert result.escalate_to == "medical"


def test_every_language_is_covered_for_the_most_serious_word():
    """The regression that started this file: one stem covering only English.

    Kept as its own test because it is the assertion that would have failed
    before the fix, and it should keep failing if a future edit drops a
    language again.
    """
    for text in ["I have depression", "У меня депрессия", "Menda depressiya bor"]:
        assert not verdict(text).allowed, f"not covered: {text}"
