"""CV assembly.

A CV is a *view* of the profile, not a copy of it. Storing snapshots would mean
every CV silently goes stale the moment a skill is verified or a course
finishes — so the document keeps only the section configuration and the payload
is assembled on demand.
"""

from __future__ import annotations

from django.conf import settings

from apps.experience.models import Experience
from apps.learning.models import Certificate, Enrollment, EnrollmentStatus
from apps.profiles.models import Education, UserSkill

from .models import CVDocument, PortfolioItem, PublicProfile


def build_cv_payload(cv: CVDocument) -> dict:
    user = cv.user
    profile = getattr(user, "student_profile", None)
    sections = cv.enabled_sections

    payload: dict = {
        "meta": {
            "id": str(cv.id),
            "title": cv.title,
            "template": cv.template,
            "language": cv.language,
            "sections": sections,
        },
        "personal": {
            "full_name": profile.full_name if profile else "",
            "headline": cv.headline,
            "youth_id": profile.youth_id if profile else None,
            "city": profile.city if profile else "",
            "region": profile.region.name if profile and profile.region else None,
            "avatar": profile.avatar.url if profile and profile.avatar else None,
        },
    }

    if "contacts" in sections:
        payload["contacts"] = {"email": user.email, "phone": user.phone}

    if "summary" in sections:
        payload["summary"] = cv.summary

    if "education" in sections:
        payload["education"] = [
            {
                "institution": e.institution,
                "degree": e.degree,
                "field_of_study": e.field_of_study,
                "start_date": e.start_date,
                "end_date": e.end_date,
                "is_current": e.is_current,
            }
            for e in Education.objects.filter(user=user)
        ]

    if "skills" in sections:
        skills = (
            UserSkill.objects.filter(user=user)
            .select_related("skill", "skill__category")
            .order_by("-proficiency")
        )
        if cv.target_profession_id:
            # Narrow to what the target role actually asks for, so the CV reads
            # as targeted rather than as a skill dump.
            relevant = set(
                cv.target_profession.skill_links.values_list("skill_id", flat=True)
            )
            skills = [s for s in skills if s.skill_id in relevant] or list(skills)
        payload["skills"] = [
            {
                "name": s.skill.name,
                "category": s.skill.category.name,
                "proficiency": s.proficiency,
                "verified": s.is_verified,
                "band": s.band,
            }
            for s in skills
        ]

    if "experience" in sections:
        payload["experience"] = [
            {
                "type": e.type,
                "title": e.title,
                "organization": e.organization,
                "description": e.description,
                "start_date": e.start_date,
                "end_date": e.end_date,
                "is_current": e.is_current,
                "duration_months": e.duration_months,
                "skills": [link.skill.name for link in e.skill_links.all()],
            }
            for e in Experience.objects.filter(user=user).prefetch_related(
                "skill_links__skill"
            )
        ]

    if "projects" in sections:
        payload["projects"] = [
            {
                "title": item.title,
                "description": item.description,
                "type": item.type,
                "url": item.url,
                "skills": [s.name for s in item.skills.all()],
            }
            for item in PortfolioItem.objects.filter(
                user=user, is_public=True
            ).prefetch_related("skills")
        ]

    if "courses" in sections:
        payload["courses"] = [
            {
                "title": e.course.title,
                "provider": e.course.employer.display_name
                if e.course.employer_id
                else settings.PLATFORM_NAME,
                "completed_at": e.completed_at,
                "level": e.course.level,
            }
            for e in Enrollment.objects.filter(
                user=user, status=EnrollmentStatus.COMPLETED
            ).select_related("course", "course__employer")
        ]

    if "certificates" in sections:
        payload["certificates"] = [
            {
                "title": c.course.title,
                "serial": c.serial,
                "issued_at": c.issued_at,
                "verification_code": c.verification_code,
            }
            for c in Certificate.objects.filter(user=user).select_related("course")
        ]

    if "languages" in sections and profile:
        payload["languages"] = profile.languages

    return payload


def build_passport_payload(public: PublicProfile) -> dict:
    """Public Youth Passport — only the blocks the owner switched on."""
    user = public.user
    profile = getattr(user, "student_profile", None)
    blocks = public.visible_blocks or {}

    payload: dict = {
        "youth_id": profile.youth_id if profile else None,
        "name": profile.full_name if profile else "",
        "headline": profile.bio[:160] if profile else "",
        "avatar": profile.avatar.url if profile and profile.avatar else None,
        "region": profile.region.name if profile and profile.region else None,
        "target_profession": profile.target_profession.name
        if profile and profile.target_profession_id
        else None,
    }

    if blocks.get("skills"):
        payload["skills"] = [
            {"name": s.skill.name, "proficiency": s.proficiency, "verified": s.is_verified}
            for s in UserSkill.objects.filter(user=user)
            .select_related("skill")
            .order_by("-proficiency")[:20]
        ]

    if blocks.get("experience"):
        payload["experience"] = [
            {
                "title": e.title,
                "organization": e.organization,
                "type": e.type,
                "duration_months": e.duration_months,
            }
            for e in Experience.objects.filter(user=user)[:10]
        ]

    if blocks.get("portfolio"):
        payload["portfolio"] = [
            {"title": p.title, "type": p.type, "url": p.url}
            for p in PortfolioItem.objects.filter(user=user, is_public=True)[:12]
        ]

    if blocks.get("certificates"):
        payload["certificates"] = [
            {"title": c.course.title, "serial": c.serial, "issued_at": c.issued_at}
            for c in Certificate.objects.filter(user=user).select_related("course")[:12]
        ]

    # Contacts are opt-in inside an already opt-in page — publishing a minor's
    # phone number should take two deliberate decisions, not one.
    if blocks.get("contacts"):
        payload["contacts"] = {"email": user.email}

    return payload
