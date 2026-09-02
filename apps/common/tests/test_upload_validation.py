"""What the upload validators must refuse.

Uploads are the one place where a stranger hands the server a file and asks it
to parse it, so the failure modes here are the ones that matter: a script
wearing a .png extension, and a picture small enough to slip past the size cap
that unpacks into gigabytes once something decodes it.

Every case asserts a *ValidationError*, not merely a failure. An upload that
blows up as an unhandled exception is a 500 and a stack trace in the log where
a plain "that file is not allowed" belonged — which is exactly what a
decompression bomb used to do here.
"""

import io

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.common.validators import validate_document_upload, validate_image_upload


def png(width: int, height: int, colour=(90, 60, 160)) -> bytes:
    image = Image.new("RGB", (width, height), colour)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=9)
    return buffer.getvalue()


def upload(name: str, payload: bytes, content_type: str = "image/png"):
    return SimpleUploadedFile(name, payload, content_type=content_type)


# -- what should be accepted ----------------------------------------------
def test_an_ordinary_photo_is_accepted():
    validate_image_upload(upload("avatar.png", png(800, 600)))


def test_a_large_but_reasonable_photo_is_accepted():
    """A 24-megapixel camera photo is a normal thing to upload."""
    validate_image_upload(upload("photo.png", png(6000, 4000)))


# -- what should be refused, and refused cleanly ---------------------------
def test_a_decompression_bomb_is_refused_rather_than_crashing():
    """The case that used to escape as a 500.

    400 megapixels of one colour is about a megabyte on disk — well under the
    size cap — and 1.2 GB once decoded. Pillow raises DecompressionBombError,
    which is not an OSError, so the validator's except clause missed it.
    """
    bomb = upload("bomb.png", png(20000, 20000))

    with pytest.raises(ValidationError):
        validate_image_upload(bomb)


def test_an_image_over_the_pixel_ceiling_is_refused():
    """Between Pillow's warning and error thresholds nothing used to stop it."""
    with pytest.raises(ValidationError):
        validate_image_upload(upload("huge.png", png(9000, 9000)))


def test_a_script_wearing_an_image_extension_is_refused():
    """The stored-XSS case: named .png, actually a page of HTML."""
    payload = b"<html><script>alert(document.cookie)</script></html>"

    with pytest.raises(ValidationError):
        validate_image_upload(upload("payload.png", payload))


def test_a_real_image_with_a_forbidden_extension_is_refused():
    """SVG can carry script, so it is not on the list however valid it is."""
    with pytest.raises(ValidationError):
        validate_image_upload(upload("logo.svg", png(20, 20), "image/svg+xml"))


def test_a_mismatched_content_type_is_refused():
    with pytest.raises(ValidationError):
        validate_image_upload(upload("avatar.png", png(20, 20), "text/html"))


def test_an_oversized_file_is_refused(settings):
    settings.MAX_UPLOAD_SIZE_MB = 1
    from apps.common import validators

    original = validators.MAX_UPLOAD_BYTES
    validators.MAX_UPLOAD_BYTES = 1024
    try:
        with pytest.raises(ValidationError):
            validate_image_upload(upload("big.png", png(1200, 1200)))
    finally:
        validators.MAX_UPLOAD_BYTES = original


# -- documents -------------------------------------------------------------
def test_a_real_pdf_is_accepted():
    validate_document_upload(
        upload("cv.pdf", b"%PDF-1.7\n%stub\n", "application/pdf")
    )


def test_a_file_pretending_to_be_a_pdf_is_refused():
    with pytest.raises(ValidationError):
        validate_document_upload(
            upload("cv.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")
        )


def test_an_executable_extension_is_refused():
    with pytest.raises(ValidationError):
        validate_document_upload(
            upload("setup.exe", b"%PDF-1.7\n", "application/pdf")
        )
