"""Upload validation.

Extension checks alone are worthless — a .png that is actually an HTML file
served back to a browser is stored XSS. We check the declared content type,
the size, and for images we let Pillow confirm the bytes really decode.

Bytes on disk are not the only limit. A single-colour PNG of 20000 x 20000
compresses to about a megabyte, sails past a 5 MB cap, and decodes to 1.2 GB
of memory in whatever opens it next — a thumbnail, a crop, a resize. So the
decoded dimensions are checked here too, before anything downstream trusts
the file.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

MAX_UPLOAD_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DOCUMENT_EXTENSIONS = {".pdf"}


def _extension(name: str) -> str:
    _, _, ext = name.rpartition(".")
    return f".{ext.lower()}" if ext else ""


def validate_upload_size(file_obj) -> None:
    if file_obj.size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            _("File is larger than %(limit)s MB.")
            % {"limit": settings.MAX_UPLOAD_SIZE_MB}
        )


def validate_image_upload(file_obj) -> None:
    validate_upload_size(file_obj)

    if _extension(file_obj.name) not in IMAGE_EXTENSIONS:
        raise ValidationError(_("Only JPEG, PNG and WebP images are allowed."))

    content_type = getattr(file_obj, "content_type", "")
    if content_type and content_type not in settings.ALLOWED_UPLOAD_IMAGE_TYPES:
        raise ValidationError(_("Unsupported image type."))

    # Confirm the bytes decode as an image, not just that the name ends in .png.
    from PIL import Image, UnidentifiedImageError

    position = file_obj.tell()
    try:
        file_obj.seek(0)
        image = Image.open(file_obj)

        # Read the dimensions from the header before decoding anything.
        width, height = image.size
        megapixels = (width * height) / 1_000_000
        if megapixels > settings.MAX_IMAGE_MEGAPIXELS:
            raise ValidationError(
                _("Image is larger than %(limit)s megapixels.")
                % {"limit": settings.MAX_IMAGE_MEGAPIXELS}
            )

        image.verify()
    except ValidationError:
        raise
    except Image.DecompressionBombError as exc:
        # Pillow spots the bomb but raises outside the OSError family, so this
        # used to escape the validator entirely and surface as a 500 — an
        # unhandled server error where a plain "that file is not allowed"
        # belonged.
        raise ValidationError(_("Image is too large to process.")) from exc
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError(_("File is not a valid image.")) from exc
    finally:
        file_obj.seek(position)


def validate_document_upload(file_obj) -> None:
    validate_upload_size(file_obj)

    if _extension(file_obj.name) not in DOCUMENT_EXTENSIONS:
        raise ValidationError(_("Only PDF documents are allowed."))

    content_type = getattr(file_obj, "content_type", "")
    if content_type and content_type not in settings.ALLOWED_UPLOAD_DOC_TYPES:
        raise ValidationError(_("Unsupported document type."))

    position = file_obj.tell()
    try:
        file_obj.seek(0)
        if file_obj.read(5) != b"%PDF-":
            raise ValidationError(_("File is not a valid PDF."))
    finally:
        file_obj.seek(position)
