"""Billing vocabulary.

Feature codes are the contract between the plans table and the code that
guards a capability. They are strings rather than model rows because a plan
can only ever grant what the code already knows how to gate — inventing a
feature row in the admin that nothing checks would be a silent no-op.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class PlanTier(models.TextChoices):
    FREE = "FREE", _("Free")
    PREMIUM = "PREMIUM", _("Premium")
    PRO = "PRO", _("Pro")
    ENTERPRISE = "ENTERPRISE", _("Enterprise")


class BillingInterval(models.TextChoices):
    MONTH = "MONTH", _("Monthly")
    YEAR = "YEAR", _("Yearly")
    NONE = "NONE", _("No recurring charge")


class SubscriptionStatus(models.TextChoices):
    TRIALING = "TRIALING", _("Trialing")
    ACTIVE = "ACTIVE", _("Active")
    PAST_DUE = "PAST_DUE", _("Past due")
    CANCELED = "CANCELED", _("Canceled")
    EXPIRED = "EXPIRED", _("Expired")

    @classmethod
    def entitling(cls) -> set[str]:
        """Statuses that still grant the plan's features.

        PAST_DUE keeps access on purpose: a failed card should trigger dunning,
        not instantly lock a student out of coursework they paid for.
        """
        return {cls.TRIALING, cls.ACTIVE, cls.PAST_DUE}


class PaymentStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    SUCCEEDED = "SUCCEEDED", _("Succeeded")
    FAILED = "FAILED", _("Failed")
    REFUNDED = "REFUNDED", _("Refunded")


class LimitKind(models.TextChoices):
    """How a numeric limit is counted.

    METERED counts consumption inside the billing period and resets when the
    period rolls over — AI messages, candidate searches.

    CONCURRENT counts live rows right now and never resets — active vacancies.
    Metering that as consumption would mean an employer who published and
    closed ten vacancies could never publish an eleventh, which is not what
    "up to 10 active vacancies" means.
    """

    METERED = "METERED", _("Consumed per period")
    CONCURRENT = "CONCURRENT", _("Concurrent objects")


class Feature(models.TextChoices):
    """Every capability the backend actually gates.

    Adding a code here is half the work; the other half is calling
    `require_feature` at the place the capability is exercised.
    """

    # Both roles
    AI_CHAT = "AI_CHAT", _("AI chat")

    # Student
    AI_ASSISTANT = "AI_ASSISTANT", _("AI career assistant")
    SKILL_GAP_ADVANCED = "SKILL_GAP_ADVANCED", _("Advanced skill gap analysis")
    CAREER_PATH_PERSONAL = "CAREER_PATH_PERSONAL", _("Personalised career path")
    CV_ANALYSIS = "CV_ANALYSIS", _("AI CV analysis")
    CV_EXPORT = "CV_EXPORT", _("CV export")
    COURSE_ENROLLMENT = "COURSE_ENROLLMENT", _("Course enrolment")
    JOB_APPLICATION = "JOB_APPLICATION", _("Job application")

    # Employer
    ACTIVE_VACANCY = "ACTIVE_VACANCY", _("Active vacancy")
    CANDIDATE_SEARCH = "CANDIDATE_SEARCH", _("Candidate search")
    CANDIDATE_ANALYTICS = "CANDIDATE_ANALYTICS", _("Candidate analytics")
    EMPLOYER_TEST = "EMPLOYER_TEST", _("Employer-authored assessment")
    EMPLOYER_COURSE = "EMPLOYER_COURSE", _("Employer-authored course")
    TALENT_PIPELINE = "TALENT_PIPELINE", _("Talent pipeline")


#: Features counted as live objects rather than per-period consumption.
CONCURRENT_FEATURES: dict[str, LimitKind] = {
    Feature.ACTIVE_VACANCY: LimitKind.CONCURRENT,
}


def limit_kind(feature: str) -> str:
    return CONCURRENT_FEATURES.get(feature, LimitKind.METERED)
