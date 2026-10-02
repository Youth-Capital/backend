"""A lesson video uploaded from the author's own computer.

Until now a VIDEO material was a YouTube or Vimeo link only. An author with the
recording on their laptop had nowhere to put it. These pin the new path: MP4 or
WebM checked by bytes, played from the signed download link, and served in
ranges so a <video> element can seek.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.common.enums import ModerationStatus

pytestmark = pytest.mark.django_db

MATERIALS_URL = "/api/v1/learning/materials/"

#: The smallest thing that passes for an MP4: a box of size 0x18 called
#: `ftyp`, brand `isom`, then some payload so ranges have bytes to cut.
MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + bytes(range(256)) * 4
WEBM_BYTES = b"\x1a\x45\xdf\xa3" + b"\x00" * 60


@pytest.fixture
def course(db, employer):
    from apps.learning.models import Course

    return Course.objects.create(
        title="Video course", slug="video-course", author=employer,
        employer=employer.employer_profile, status=ModerationStatus.PUBLISHED,
    )


@pytest.fixture
def lesson(db, course):
    from apps.learning.models import CourseModule, Lesson

    module = CourseModule.objects.create(course=course, title="M", order=0)
    return Lesson.objects.create(module=module, title="L", order=0)


def upload(client, course, lesson, name, data, content_type):
    return client.post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(lesson.id),
            "kind": "VIDEO",
            "title": "Lecture",
            "file": SimpleUploadedFile(name, data, content_type=content_type),
        },
        format="multipart",
    )


def test_an_mp4_from_the_laptop_is_accepted(auth, employer, course, lesson):
    response = upload(auth(employer), course, lesson, "lecture.mp4", MP4_BYTES, "video/mp4")

    assert response.status_code == 201, response.data
    video = response.data["video"]
    assert video["provider"] == "upload"
    assert video["embed_url"] is None
    # The player plays the platform's own signed link, never an author's URL.
    assert "/api/v1/files/course-material/" in video["url"]
    assert video["url"] == response.data["file_url"]


def test_a_webm_is_accepted(auth, employer, course, lesson):
    response = upload(auth(employer), course, lesson, "lecture.webm", WEBM_BYTES, "video/webm")

    assert response.status_code == 201, response.data


def test_a_renamed_file_is_refused(auth, employer, course, lesson):
    """Called .mp4, but it is not one — it would play as a black box."""
    response = upload(
        auth(employer), course, lesson, "lecture.mp4", b"not a video at all", "video/mp4"
    )

    assert response.status_code == 400


def test_a_format_browsers_cannot_play_is_refused(auth, employer, course, lesson):
    response = upload(
        auth(employer), course, lesson, "lecture.avi", MP4_BYTES, "video/x-msvideo"
    )

    assert response.status_code == 400


def test_a_youtube_link_still_works(auth, employer, course, lesson):
    response = auth(employer).post(
        MATERIALS_URL,
        {
            "course": str(course.id),
            "lesson": str(lesson.id),
            "kind": "VIDEO",
            "title": "Talk",
            "url": "https://youtu.be/dQw4w9WgXcQ",
        },
        format="json",
    )

    assert response.status_code == 201, response.data
    assert response.data["video"]["provider"] == "youtube"


def test_a_video_needs_a_file_or_a_link(auth, employer, course, lesson):
    response = auth(employer).post(
        MATERIALS_URL,
        {"course": str(course.id), "kind": "VIDEO", "title": "Nothing"},
        format="json",
    )

    assert response.status_code == 400


# -- playing it -------------------------------------------------------------
def material_path(auth, employer, course, lesson):
    response = upload(auth(employer), course, lesson, "lecture.mp4", MP4_BYTES, "video/mp4")
    return f"/api/v1/files/course-material/{response.data['id']}/"


def body(response):
    return b"".join(response.streaming_content)


def test_a_range_request_gets_exactly_those_bytes(auth, employer, course, lesson):
    path = material_path(auth, employer, course, lesson)

    response = auth(employer).get(path, HTTP_RANGE="bytes=4-11")

    assert response.status_code == 206
    assert response["Content-Range"] == f"bytes 4-11/{len(MP4_BYTES)}"
    assert body(response) == MP4_BYTES[4:12]
    assert response["Content-Type"] == "video/mp4"


def test_an_open_ended_range_runs_to_the_end(auth, employer, course, lesson):
    path = material_path(auth, employer, course, lesson)

    response = auth(employer).get(path, HTTP_RANGE="bytes=1000-")

    assert response.status_code == 206
    assert body(response) == MP4_BYTES[1000:]


def test_a_suffix_range_is_the_last_bytes(auth, employer, course, lesson):
    path = material_path(auth, employer, course, lesson)

    response = auth(employer).get(path, HTTP_RANGE="bytes=-10")

    assert body(response) == MP4_BYTES[-10:]


def test_a_nonsense_range_gets_the_whole_file(auth, employer, course, lesson):
    path = material_path(auth, employer, course, lesson)

    response = auth(employer).get(path, HTTP_RANGE="bytes=99999999-")

    assert response.status_code == 200
    assert body(response) == MP4_BYTES
    assert response["Accept-Ranges"] == "bytes"


def test_ranges_do_not_bypass_the_access_rule(
    auth, employer, other_employer, course, lesson
):
    """A range is still a read, and still answers to the course's rule."""
    path = material_path(auth, employer, course, lesson)

    response = auth(other_employer).get(path, HTTP_RANGE="bytes=0-11")

    assert response.status_code == 404


def test_a_course_material_link_outlives_ten_minutes(
    auth, employer, course, lesson, monkeypatch
):
    """A learner seeking half an hour into a lecture must not hit a dead link."""
    import time

    from apps.common.api import files

    response = upload(auth(employer), course, lesson, "lecture.mp4", MP4_BYTES, "video/mp4")
    link = response.data["file_url"]
    token = link.split("?t=", 1)[1]

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 60 * 60)

    assert files._link_holder(token, "course-material", response.data["id"]) is not None
