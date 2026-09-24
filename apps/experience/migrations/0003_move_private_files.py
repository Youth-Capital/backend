"""Move project assets out of the public media directory."""

from django.db import migrations


def move_to_private(apps, schema_editor):
    from apps.common.storage import relocate_to_private

    relocate_to_private(apps.get_model("experience", "ProjectAsset"), "file")


def move_back(apps, schema_editor):
    from apps.common.storage import relocate_to_private

    relocate_to_private(
        apps.get_model("experience", "ProjectAsset"), "file", reverse=True
    )


class Migration(migrations.Migration):
    dependencies = [
        ("experience", "0002_alter_projectasset_file"),
    ]

    operations = [migrations.RunPython(move_to_private, move_back)]
