"""Move course material and certificates out of the public media directory.

The field change in 0010 tells Django where to *look*; it does not move the
bytes. Without this, every file uploaded before the change becomes a broken
link — and, worse, stays readable at its old public address.
"""

from django.db import migrations


def move_to_private(apps, schema_editor):
    from apps.common.storage import relocate_to_private

    for model_name, field in (("CourseMaterial", "file"), ("Certificate", "file")):
        relocate_to_private(apps.get_model("learning", model_name), field)


def move_back(apps, schema_editor):
    from apps.common.storage import relocate_to_private

    for model_name, field in (("CourseMaterial", "file"), ("Certificate", "file")):
        relocate_to_private(apps.get_model("learning", model_name), field, reverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ("learning", "0010_alter_certificate_file_alter_coursematerial_file"),
    ]

    operations = [migrations.RunPython(move_to_private, move_back)]
