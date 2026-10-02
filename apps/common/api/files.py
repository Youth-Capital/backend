"""One door for every private file.

Uploads used to be answered by the web server straight out of `MEDIA_ROOT`,
which meant the platform never saw the request and so never got to ask whether
the person was allowed the file. A CV, somebody's portfolio, a paid course book
— all of them answered `200` to anybody who knew the path, and the paths were
guessable because Django keeps the name the browser sent.

Private files now live outside anything the web server publishes, and this view
is the only way to one. Each kind of file names the rule that governs it, in one
place, next to the query that fetches it — so "who may read this" is a property
of the file type rather than something each serialiser remembers to think about.

Two deliberate choices:

* A refusal is `404`, not `403`. Confirming that a file exists but belongs to
  somebody else is itself a small leak — it turns this endpoint into an oracle
  for whether a given id is real.
* Nothing is public by default. A kind that is not in the registry has no URL
  at all, so adding a private file field and forgetting to think about access
  produces a file nobody can reach, rather than one everybody can.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from django.http import FileResponse, Http404, StreamingHttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotAuthenticated
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView


@dataclass(frozen=True)
class FileKind:
    """A private file field, and who is allowed to read it."""

    #: Returns the model instance, or raises the model's DoesNotExist.
    fetch: Callable
    #: Attribute on that instance holding the FileField.
    field: str
    #: (user, instance) -> bool
    may_read: Callable
    #: How long a signed link to this kind keeps working. See LINK_TTL_SECONDS.
    ttl: int | None = None


# ---------------------------------------------------------------------------
# The rules
# ---------------------------------------------------------------------------
def _portfolio_item(pk):
    from apps.cv.models import PortfolioItem

    return PortfolioItem.objects.select_related("user").get(pk=pk)


def _may_read_portfolio(user, item) -> bool:
    """The owner always; anyone else only through a profile made public.

    A portfolio is the one private thing a learner may deliberately publish —
    the Youth Passport. `can_view_student_profile` already decides who counts
    as an audience for that, and reusing it means the file and the page it
    belongs to cannot drift into disagreeing.
    """
    from apps.profiles.services import can_view_student_profile

    if item.user_id == user.id or user.is_admin:
        return True
    return can_view_student_profile(viewer=user, student_user=item.user)


def _project_asset(pk):
    from apps.experience.models import ProjectAsset

    return ProjectAsset.objects.select_related("experience__user").get(pk=pk)


def _may_read_experience_asset(user, asset) -> bool:
    from apps.profiles.services import can_view_student_profile

    owner = asset.experience.user
    if owner.id == user.id or user.is_admin:
        return True
    return can_view_student_profile(viewer=user, student_user=owner)


def _course_material(pk):
    from apps.learning.models import CourseMaterial

    return CourseMaterial.objects.select_related("course", "lesson__module__course").get(
        pk=pk
    )


def _may_read_course_material(user, material) -> bool:
    """Exactly the rule that governs the lesson the material sits in."""
    from apps.learning.services import can_open_course, can_open_lesson

    if material.lesson_id:
        return can_open_lesson(user, material.lesson)
    return can_open_course(user, material.course)


def _certificate(pk):
    from apps.learning.models import Certificate

    return Certificate.objects.select_related("user").get(pk=pk)


def _may_read_certificate(user, certificate) -> bool:
    return certificate.user_id == user.id or user.is_admin


#: How long a link handed out by the API keeps working. Long enough to click
#: after the page loads; short enough that a link copied into a chat is dead by
#: the time anyone else tries it.
LINK_TTL_SECONDS = 10 * 60

#: Course materials include uploaded lesson videos, and a <video> element keeps
#: fetching from the address it was given for as long as the lesson is open —
#: every seek is a new request. Ten minutes would break the player partway
#: through a longer lesson. A few hours covers a sitting; the link still names
#: the one person it was issued to, and the view still asks whether that person
#: may open the course, so a learner who loses access loses the video too.
COURSE_MATERIAL_TTL_SECONDS = 4 * 60 * 60

REGISTRY: dict[str, FileKind] = {
    "portfolio-item": FileKind(_portfolio_item, "file", _may_read_portfolio),
    "portfolio-cover": FileKind(_portfolio_item, "cover", _may_read_portfolio),
    "experience-asset": FileKind(
        _project_asset, "file", _may_read_experience_asset
    ),
    "course-material": FileKind(
        _course_material,
        "file",
        _may_read_course_material,
        ttl=COURSE_MATERIAL_TTL_SECONDS,
    ),
    "certificate": FileKind(_certificate, "file", _may_read_certificate),
}
_SALT = "apps.common.protected-file"


def file_url(kind: str, obj, request=None) -> str | None:
    """The address a client should use for this file, or None if there is none.

    Serialisers call this instead of `field.url`. Private storage has no
    `base_url`, so `field.url` raises by design — there is no public address to
    hand out, and that is the point.

    The link carries a signature naming the person it was issued to and the one
    file it opens. That is what lets a plain `<a href>` or `<img src>` work: the
    SPA's access token lives in an Authorization header, and a browser following
    a link sends no header, so an unsigned link answered 401 in the interface.
    The signature is not a free pass — the view still asks whether that person
    may read the file, so losing access kills a link already handed out.
    """
    from django.core import signing

    field = getattr(obj, REGISTRY[kind].field, None)
    if not field:
        return None
    path = f"/api/v1/files/{kind}/{obj.pk}/"

    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        token = signing.dumps(
            {"k": kind, "o": str(obj.pk), "u": str(user.pk)}, salt=_SALT, compress=True
        )
        path = f"{path}?t={token}"
    return request.build_absolute_uri(path) if request is not None else path


def _link_holder(token: str, kind: str, pk):
    """The person a signed link was issued to, or None if it is not valid here."""
    from django.core import signing

    from apps.accounts.models import User

    ttl = REGISTRY[kind].ttl or LINK_TTL_SECONDS
    try:
        claim = signing.loads(token, salt=_SALT, max_age=ttl)
    except signing.BadSignature:  # includes SignatureExpired
        return None
    if claim.get("k") != kind or claim.get("o") != str(pk):
        return None
    return User.objects.filter(pk=claim.get("u"), is_active=True).first()


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------
@extend_schema(tags=["files"])
class ProtectedFileView(APIView):
    """Serve a private upload, after asking whether this person may have it."""

    #: Who is asking is established below — from the Authorization header, or
    #: from a signed link — and then checked against the file's own rule.
    #: AllowAny here is not "anyone may download"; it is "the answer to who you
    #: are may arrive in either of two forms".
    permission_classes = [AllowAny]

    def get(self, request, kind: str, pk):
        rule = REGISTRY.get(kind)
        if rule is None:
            raise Http404

        reader = request.user if request.user.is_authenticated else None
        token = request.query_params.get("t")
        if token:
            holder = _link_holder(token, kind, pk)
            # A bad, expired or misdirected link, or one being used by somebody
            # signed in as a different person, is simply not a link to this file.
            if holder is None or (reader is not None and reader.pk != holder.pk):
                raise Http404
            reader = holder
        if reader is None:
            raise NotAuthenticated()

        try:
            obj = rule.fetch(pk)
        except Exception as exc:  # DoesNotExist, or a malformed id
            raise Http404 from exc

        if not rule.may_read(reader, obj):
            # Deliberately indistinguishable from "no such file".
            raise Http404

        stored = getattr(obj, rule.field, None)
        if not stored:
            raise Http404

        try:
            handle = stored.open("rb")
        except FileNotFoundError as exc:
            raise Http404 from exc

        filename = stored.name.rsplit("/", 1)[-1]
        partial = _byte_range(request.META.get("HTTP_RANGE", ""), stored.size)
        if partial is not None:
            response = _partial_response(handle, filename, *partial, stored.size)
        else:
            # `as_attachment` keeps the browser from rendering an upload
            # inline: an uploaded SVG or HTML file rendered on our own origin
            # would be a stored cross-site scripting hole.
            response = FileResponse(handle, as_attachment=True, filename=filename)
        # A <video> element plays straight from here, and without ranges it
        # can neither seek nor start an MP4 whose index sits at the end of the
        # file until the whole thing has downloaded.
        response["Accept-Ranges"] = "bytes"
        # Somebody's own file, possibly fetched through a signed link: no
        # shared cache may keep a copy, and no page it is opened from learns
        # the link through its Referer.
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 64 * 1024


def _byte_range(header: str, size: int) -> tuple[int, int] | None:
    """The single byte range asked for, as (first, last) inclusive, or None.

    Only one range: that is all a media player asks for, and multipart
    responses buy nothing here. Anything unparseable, or outside the file,
    falls back to the whole file rather than an error — the client gets more
    than it asked for, which it can always cope with.
    """
    match = _RANGE.match(header.strip())
    if not match or size <= 0:
        return None
    start, end = match.groups()
    if start == "" and end == "":
        return None
    if start == "":
        # "bytes=-500": the last 500 bytes.
        first = max(size - int(end), 0)
        last = size - 1
    else:
        first = int(start)
        last = min(int(end), size - 1) if end else size - 1
    if first > last or first >= size:
        return None
    return first, last


def _partial_response(handle, filename: str, first: int, last: int, size: int):
    import mimetypes

    def chunks():
        try:
            handle.seek(first)
            remaining = last - first + 1
            while remaining > 0:
                data = handle.read(min(_CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data
        finally:
            handle.close()

    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    response = StreamingHttpResponse(chunks(), status=206, content_type=content_type)
    response["Content-Range"] = f"bytes {first}-{last}/{size}"
    response["Content-Length"] = str(last - first + 1)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
