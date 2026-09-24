"""Where uploaded files live, and what they are called.

Two separate problems, both solved here because both are about the file before
anyone asks for it.

**Where.** Everything used to land in `MEDIA_ROOT`, which is a directory a web
server hands out to anyone who asks for the right path. That is correct for a
company logo and wrong for a CV: the platform's own permission checks never ran,
because the request never reached the platform. Private files now live in a
second root that nothing is configured to serve, so the only way to one is
through `apps/common/api/files.py`, which asks who is asking.

**What they are called.** Django keeps the name the browser sent and appends a
short suffix only on collision, so the *first* `resume.pdf` is stored as exactly
`resume.pdf`. That made the old media directory guessable — `book.epub`,
`resume.pdf`, `photo.jpg` — and a guessable name is a password everybody knows.
Names are now a UUID plus the original extension, which leaks neither who
uploaded it nor what they called it.

Public files keep the default storage. Being public is a decision made here,
once, per field — not an accident of where the file happened to be written.
"""

from __future__ import annotations

import posixpath
import uuid
from pathlib import Path

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.functional import cached_property


class PrivateMediaStorage(FileSystemStorage):
    """A root outside anything the web server publishes.

    `url()` raises instead of answering. Passing `base_url=None` is not enough
    on its own — `FileSystemStorage` falls back to `settings.MEDIA_URL`, so
    `file.url` would hand back `/media/portfolio/items/....pdf`: an address
    that looks public, is not served, and would quietly reappear in a template
    or a serialiser as a broken link. Worse, it would look like the old
    behaviour was still there.

    Raising makes that a loud mistake at the one moment it can still be fixed.
    Code that needs a link asks `apps/common/api/files.file_url` for one.
    """

    @cached_property
    def base_location(self):
        # Read from settings when first used rather than when this module is
        # imported, so the test settings' scratch directory is the one used.
        return self._value_or_setting(self._location, settings.PRIVATE_MEDIA_ROOT)

    def url(self, name):
        raise ValueError(
            "Private files have no public URL. "
            "Use apps.common.api.files.file_url() to build a checked download link."
        )


private_storage = PrivateMediaStorage()

#: Extensions an upload is allowed to keep. Anything else is stored as `.bin`,
#: so a file cannot carry an extension the server might later act on.
SAFE_EXTENSIONS = frozenset(
    {
        ".pdf", ".epub", ".fb2", ".txt", ".md", ".rtf",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv",
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg",
        ".mp3", ".mp4", ".m4a", ".wav", ".webm", ".mov",
        ".zip",
    }
)


def _stored_name(prefix: str, filename: str) -> str:
    """Throw away the name the client chose, keep a vetted extension.

    The original name is not information the storage layer needs, and it is
    information an attacker can use: it makes paths guessable and it can carry
    a traversal attempt or a second extension. Only a known suffix survives,
    because that is what a download needs in order to be opened by the right
    application later.
    """
    extension = Path(filename or "").suffix.lower()
    if extension not in SAFE_EXTENSIONS:
        extension = ".bin"
    return posixpath.join(prefix, f"{uuid.uuid4().hex}{extension}")


# Each destination is a plain module-level function rather than something a
# factory returns: migrations serialise an `upload_to` by its import path, and
# a closure has no importable name to write down.
def portfolio_item_path(instance, filename):
    return _stored_name("portfolio/items", filename)


def portfolio_cover_path(instance, filename):
    return _stored_name("portfolio/covers", filename)


def experience_asset_path(instance, filename):
    return _stored_name("portfolio/experience", filename)


def course_material_path(instance, filename):
    return _stored_name("course-materials", filename)


def certificate_path(instance, filename):
    return _stored_name("certificates", filename)


def relocate_to_private(model, field_name: str, *, reverse: bool = False) -> int:
    """Move files already on disk between the public and the private root.

    Changing a field's storage changes where Django *looks*, not where the
    bytes are, so without this every file uploaded before the change turns into
    a broken link. Rows are left alone when the file is already missing — a
    half-migrated directory is worth more than a migration that refuses to run.

    Names are not rewritten to UUIDs here. A rename would be an improvement for
    old files too, but it makes the operation irreversible in practice, and the
    security property that matters — the file is no longer in a directory the
    web server publishes — is delivered by the move alone. New uploads get a
    UUID from the `upload_to` above.
    """
    import shutil

    source_root = Path(settings.MEDIA_ROOT if not reverse else settings.PRIVATE_MEDIA_ROOT)
    target_root = Path(settings.PRIVATE_MEDIA_ROOT if not reverse else settings.MEDIA_ROOT)

    moved = 0
    for pk, name in model.objects.exclude(**{field_name: ""}).values_list(
        "pk", field_name
    ):
        if not name:
            continue
        source = source_root / name
        target = target_root / name
        if target.exists() or not source.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        moved += 1
    return moved
