"""Asking for feedback, and reading what comes back.

The rules that matter are all in :func:`request_review`. Everything else is
bookkeeping.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.common.enums import Priority, Role
from apps.common.exceptions import Conflict, DomainError

from .models import (
    CampaignStatus,
    PlatformReview,
    ReviewCampaign,
    ReviewRequest,
    ReviewTrigger,
)

logger = logging.getLogger(__name__)

#: Nobody is asked twice inside this window, whatever triggered it. A person
#: who finishes two courses in a week has not become twice as opinionated.
ASK_COOLDOWN_DAYS = 45
#: After writing a review, left alone for this long. The opinion has been
#: recorded; asking again is not research, it is nagging.
REVIEW_COOLDOWN_DAYS = 120
#: "Not now" is honoured for this long, then the person may be asked again.
DISMISS_COOLDOWN_DAYS = 60
#: An account younger than this has nothing to review yet.
MIN_ACCOUNT_AGE_DAYS = 14


def can_ask(user, *, now=None) -> bool:
    """Whether this person may be asked for feedback right now."""
    now = now or timezone.now()

    if user.date_joined > now - timedelta(days=MIN_ACCOUNT_AGE_DAYS):
        return False

    recent_review = PlatformReview.objects.filter(
        user=user, created_at__gte=now - timedelta(days=REVIEW_COOLDOWN_DAYS)
    ).exists()
    if recent_review:
        return False

    recent_ask = ReviewRequest.objects.filter(
        user=user, notified_at__gte=now - timedelta(days=ASK_COOLDOWN_DAYS)
    ).exists()
    if recent_ask:
        return False

    dismissed = ReviewRequest.objects.filter(
        user=user, dismissed_at__gte=now - timedelta(days=DISMISS_COOLDOWN_DAYS)
    ).exists()
    return not dismissed


def request_review(
    user,
    *,
    trigger: str,
    campaign: ReviewCampaign | None = None,
    ref_type: str = "",
    ref_id=None,
    force: bool = False,
) -> ReviewRequest | None:
    """Ask one person for a review, and notify them.

    Returns ``None`` when the rules say not to ask — which is most of the time,
    and is the point. Never raises: an unsent invitation must not fail the
    course completion or placement that triggered it.
    """
    from apps.notifications.services import notify

    try:
        if not force and not can_ask(user):
            return None

        request, created = ReviewRequest.objects.get_or_create(
            user=user,
            campaign=campaign,
            defaults={
                "trigger": trigger,
                "ref_type": ref_type,
                "ref_id": ref_id,
            },
        )
        if not created and not request.is_open:
            return None

        notify(
            user=user,
            type="REVIEW_REQUEST",
            title_key="notifications.review.request.title",
            body_key="notifications.review.request.body",
            payload={
                "trigger": trigger,
                "campaign": campaign.title if campaign else "",
                "request_id": str(request.id),
            },
            ref_type="ReviewRequest",
            ref_id=request.id,
            action_url=f"/feedback/review?request={request.id}",
            priority=Priority.LOW,
        )
        request.notified_at = timezone.now()
        request.save(update_fields=["notified_at", "updated_at"])
        return request
    except Exception:  # pragma: no cover - never break the caller
        logger.exception("Failed to request a review from user=%s", getattr(user, "id", None))
        return None


@transaction.atomic
def launch_campaign(campaign: ReviewCampaign, *, actor=None) -> int:
    """Invite everyone the campaign targets. Returns how many were asked.

    Re-runnable: people already invited to this campaign are skipped by the
    unique constraint, so a launch interrupted halfway can simply be run again.
    """
    from apps.accounts.models import User

    if campaign.status == CampaignStatus.FINISHED:
        raise Conflict("This campaign is already finished.", code="campaign_finished")

    now = timezone.now()
    audience = User.objects.filter(
        is_active=True,
        date_joined__lte=now - timedelta(days=campaign.min_account_age_days),
    ).exclude(role=Role.ADMIN)

    if campaign.audience_roles:
        audience = audience.filter(role__in=campaign.audience_roles)

    already = set(
        ReviewRequest.objects.filter(campaign=campaign).values_list("user_id", flat=True)
    )

    invited = 0
    for user in audience.iterator(chunk_size=200):
        if user.id in already:
            continue
        if request_review(
            user, trigger=ReviewTrigger.CAMPAIGN, campaign=campaign
        ) is not None:
            invited += 1

    campaign.status = CampaignStatus.RUNNING
    campaign.launched_at = campaign.launched_at or now
    campaign.invited_count = ReviewRequest.objects.filter(campaign=campaign).count()
    campaign.save(
        update_fields=["status", "launched_at", "invited_count", "updated_at"]
    )

    from apps.audit.models import AuditAction, AuditSeverity
    from apps.audit.services import log_action

    log_action(
        action=AuditAction.CONFIG_CHANGE,
        obj=campaign,
        actor=actor,
        note=f"review campaign launched, {invited} invited",
        severity=AuditSeverity.NOTICE,
    )
    return invited


@transaction.atomic
def submit_review(
    user,
    *,
    rating: int,
    pros: str = "",
    cons: str = "",
    suggestion: str = "",
    nps_score: int | None = None,
    is_anonymous: bool = False,
    request: ReviewRequest | None = None,
) -> PlatformReview:
    """Record one review and close the invitation that prompted it."""
    if not 1 <= int(rating) <= 5:
        raise DomainError("Rating must be between 1 and 5.", code="rating_range")
    if nps_score is not None and not 0 <= int(nps_score) <= 10:
        raise DomainError("NPS must be between 0 and 10.", code="nps_range")

    if request is not None and request.user_id != user.id:
        raise DomainError("That invitation is not yours.", code="not_your_request")
    if request is not None and getattr(request, "review", None) is not None:
        raise Conflict("This invitation was already answered.", code="already_answered")

    review = PlatformReview.objects.create(
        user=user,
        request=request,
        campaign=request.campaign if request else None,
        role_at_review=user.role,
        trigger=request.trigger if request else ReviewTrigger.MANUAL,
        rating=int(rating),
        nps_score=nps_score,
        pros=pros[:2000],
        cons=cons[:2000],
        suggestion=suggestion[:2000],
        is_anonymous=is_anonymous,
    )

    if request is not None:
        request.responded_at = timezone.now()
        request.save(update_fields=["responded_at", "updated_at"])
        if request.campaign_id:
            ReviewCampaign.objects.filter(pk=request.campaign_id).update(
                responded_count=PlatformReview.objects.filter(
                    campaign_id=request.campaign_id
                ).count()
            )

    from apps.analytics.services import track

    track(
        user,
        "platform_review_submitted",
        {"rating": review.rating, "nps": nps_score, "role": user.role},
    )
    return review


def dismiss_request(request: ReviewRequest) -> ReviewRequest:
    """"Not now." Honoured for a period, not forever."""
    request.dismissed_at = timezone.now()
    request.save(update_fields=["dismissed_at", "updated_at"])
    return request


def open_request_for(user) -> ReviewRequest | None:
    """The invitation the UI should surface, if any."""
    return (
        ReviewRequest.objects.filter(
            user=user, responded_at__isnull=True, dismissed_at__isnull=True
        )
        .select_related("campaign")
        .order_by("-created_at")
        .first()
    )


# ---------------------------------------------------------------------------
# Reading the results
# ---------------------------------------------------------------------------
def review_stats(*, role: str | None = None, since=None) -> dict:
    """Aggregate for the admin dashboard.

    Counts every submitted review, moderated or not: moderation decides what
    may be shown publicly, not what is true about how people feel.
    """
    queryset = PlatformReview.objects.all()
    if role:
        queryset = queryset.filter(role_at_review=role)
    if since:
        queryset = queryset.filter(created_at__gte=since)

    totals = queryset.aggregate(
        count=Count("id"),
        average=Avg("rating"),
        nps_average=Avg("nps_score"),
        promoters=Count("id", filter=Q(nps_score__gte=9)),
        detractors=Count("id", filter=Q(nps_score__lte=6, nps_score__isnull=False)),
        rated=Count("id", filter=Q(nps_score__isnull=False)),
    )

    count = totals["count"] or 0
    rated = totals["rated"] or 0
    # Net Promoter Score: promoters minus detractors, as a percentage of those
    # who answered the NPS question — not of everyone, which would drag every
    # score toward zero as the optional field goes unanswered.
    nps = (
        round(100 * (totals["promoters"] - totals["detractors"]) / rated)
        if rated
        else None
    )

    distribution = {
        str(row["rating"]): row["n"]
        for row in queryset.values("rating").annotate(n=Count("id"))
    }
    by_role = {
        row["role_at_review"]: {
            "count": row["n"],
            "average": round(row["avg"] or 0, 2),
        }
        for row in queryset.values("role_at_review").annotate(
            n=Count("id"), avg=Avg("rating")
        )
    }

    return {
        "count": count,
        "average": round(totals["average"] or 0, 2),
        "nps": nps,
        "nps_average": round(totals["nps_average"] or 0, 2),
        "promoters": totals["promoters"],
        "detractors": totals["detractors"],
        "distribution": {str(i): distribution.get(str(i), 0) for i in range(1, 6)},
        "by_role": by_role,
        "pending_moderation": queryset.filter(status="PENDING_REVIEW").count(),
        "with_cons": queryset.exclude(cons="").count(),
    }
