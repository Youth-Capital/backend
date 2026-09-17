"""Whether a vacancy is the kind of work this person is actually after.

The six-component score answers "can they do this job". It says nothing about
whether they want it, and the pool it draws from was built on skill overlap
alone — so a learner heading for data analytics, who has Python and SQL,
was matched against every backend, DevOps and security vacancy that also
wanted Python or SQL. The scores were correct. The list was useless.

RELEVANCE IS A SEPARATE NUMBER, ON PURPOSE.

The obvious implementation is to fold interest into the match score as a
seventh component. That is wrong twice over:

  * It destroys information. A job somebody is perfectly suited for but does
    not want, and a job they want but cannot do, would come out at the same
    middling number — and those are opposite situations needing opposite
    advice. Kept apart, the platform can say "this fits you but it is not
    the direction you named" and "this is your direction, here is the gap".
  * It silently redefines a number employers already read. The candidate list
    sorts on that score and people have calibrated on it; quietly mixing in a
    preference signal changes what every stored result means.

So relevance is computed here, stored beside the score, and used to decide
*what appears in a feed* — never to alter what a match is worth.

WHERE THE SIGNAL COMES FROM

Everything is read from the taxonomy the platform already maintains. Nothing
is inferred from free text and nothing is guessed:

  1. the profession the learner named as their target, against the vacancy's
     profession — the strongest signal there is, because they typed it;
  2. the skills that profession requires, against the skills the vacancy
     requires — this is what catches the neighbouring job with a different
     title, which a title match alone would throw away;
  3. the skill categories the learner ticked as interests, against the
     categories the vacancy's skills belong to — broader, weaker, and the
     only signal a learner who has not picked a target profession has.

THE COLD START IS THE PART THAT MATTERS

A learner who has named no interests and no target profession has no signal
at all, and filtering on a signal that does not exist would hand them an
empty feed — which is a worse product than the noisy one being fixed. When
there is nothing to go on this module says so, explicitly, and the caller
falls back to ranking by fit alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: A vacancy in the profession the learner named. Nothing else is this strong:
#: they typed it into their own profile.
TARGET_PROFESSION_SCORE = 100

#: The floor a vacancy has to clear to appear in a learner's recommendations.
#:
#: Set where it is because of what sits either side. Above it: the named
#: profession, a neighbouring one sharing most of its skills, and anything in
#: a category the learner ticked. Below it: a vacancy that overlaps on one
#: generic skill and nothing else, which is exactly the Python-and-therefore-
#: DevOps case this module exists to remove.
RELEVANT_ENOUGH = 35


@dataclass
class Relevance:
    """How close this vacancy is to what the learner said they want."""

    score: int = 0
    #: False when the learner has told the platform nothing to go on. The
    #: caller must not filter on the score in that case — see the module note.
    known: bool = False
    reasons: list[dict] = field(default_factory=list)

    @property
    def relevant(self) -> bool:
        """Unknown counts as relevant: silence is not a rejection."""
        return not self.known or self.score >= RELEVANT_ENOUGH


def _profession_skill_ids(profession) -> set:
    from apps.taxonomy.models import ProfessionSkill

    if profession is None:
        return set()
    return set(
        ProfessionSkill.objects.filter(profession=profession).values_list(
            "skill_id", flat=True
        )
    )


def _vacancy_skill_ids(vacancy) -> set:
    return set(vacancy.skill_links.values_list("skill_id", flat=True))


def _vacancy_category_ids(vacancy) -> set:
    from apps.jobs.models import VacancySkill

    return set(
        VacancySkill.objects.filter(vacancy=vacancy)
        .values_list("skill__category_id", flat=True)
        .distinct()
    )


def for_student(student, vacancy) -> Relevance:
    """How relevant this vacancy is to this learner.

    Reads the profile once and the taxonomy once; callers scoring a whole pool
    should hoist that work out — see `RelevanceContext` below.
    """
    profile = getattr(student, "student_profile", None)
    if profile is None:
        return Relevance(score=0, known=False)

    return RelevanceContext.for_student(student, profile=profile).score_vacancy(vacancy)


class RelevanceContext:
    """Everything about one learner that relevance needs, read once.

    Scoring a pool of two hundred vacancies used to mean two hundred round
    trips for the same profile and the same profession skill list. The context
    is the fix, and it is a class rather than a cache because the lifetime
    should be one request, not one process — an interest ticked mid-session
    must take effect on the next page, not after a deploy.
    """

    def __init__(self, *, target_profession, target_skill_ids, interest_category_ids):
        self.target_profession = target_profession
        self.target_skill_ids = target_skill_ids
        self.interest_category_ids = interest_category_ids

    @classmethod
    def for_student(cls, student, *, profile=None) -> "RelevanceContext":
        profile = profile or getattr(student, "student_profile", None)
        if profile is None:
            return cls(
                target_profession=None, target_skill_ids=set(), interest_category_ids=set()
            )

        target = profile.target_profession
        return cls(
            target_profession=target,
            target_skill_ids=_profession_skill_ids(target),
            interest_category_ids=set(
                profile.interests.values_list("id", flat=True)
            ),
        )

    @property
    def known(self) -> bool:
        """Whether the learner has told us anything to filter on."""
        return bool(self.target_profession or self.interest_category_ids)

    def score_vacancy(self, vacancy) -> Relevance:
        if not self.known:
            return Relevance(
                score=0,
                known=False,
                reasons=[{"code": "no_interests_declared"}],
            )

        reasons: list[dict] = []
        score = 0

        # 1. The profession they named.
        if (
            self.target_profession is not None
            and vacancy.profession_id == self.target_profession.id
        ):
            return Relevance(
                score=TARGET_PROFESSION_SCORE,
                known=True,
                reasons=[
                    {
                        "code": "target_profession",
                        "profession": str(self.target_profession),
                    }
                ],
            )

        vacancy_skills = _vacancy_skill_ids(vacancy)

        # 2. The skills that profession needs, against the ones this job needs.
        #
        # Measured against the *vacancy's* requirement list rather than the
        # profession's: the question is what share of this job the learner's
        # target already covers. Against the profession's list instead, a
        # narrow vacancy asking for one skill out of twelve would score 8%
        # and be dropped, when in fact it is entirely within their direction.
        if self.target_skill_ids and vacancy_skills:
            shared = self.target_skill_ids & vacancy_skills
            if shared:
                overlap = round(100 * len(shared) / len(vacancy_skills))
                # Capped below the named profession: a neighbouring job is a
                # good suggestion, never a better one than the thing they
                # actually asked for.
                score = max(score, min(85, overlap))
                reasons.append(
                    {
                        "code": "shares_target_skills",
                        "shared": len(shared),
                        "of": len(vacancy_skills),
                    }
                )

        # 3. The categories they ticked.
        if self.interest_category_ids:
            categories = _vacancy_category_ids(vacancy)
            shared_categories = self.interest_category_ids & categories
            if shared_categories:
                overlap = round(100 * len(shared_categories) / max(1, len(categories)))
                # Weaker than a skill match by design: "interested in IT" is a
                # much softer claim than "needs the same six skills".
                score = max(score, min(70, 40 + overlap // 2))
                reasons.append(
                    {"code": "declared_interest", "categories": len(shared_categories)}
                )

        if not reasons:
            reasons.append({"code": "outside_declared_interests"})

        return Relevance(score=score, known=True, reasons=reasons)


def for_vacancy(vacancy, student) -> Relevance:
    """The employer's side of the same question.

    Deliberately the same computation, not a mirrored one. Relevance is a
    property of the pair — whether this person wants this kind of work — and
    it does not change depending on who is asking. An employer looking at a
    candidate whose stated direction is elsewhere is looking at somebody
    likely to decline, and that is worth knowing before an interview is
    arranged rather than after.
    """
    return for_student(student, vacancy)
