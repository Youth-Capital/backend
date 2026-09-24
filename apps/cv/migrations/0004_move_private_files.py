"""Move portfolio uploads out of the public media directory.

See apps/common/storage.py: 0003 changed where Django looks, this moves the
files so the change does not orphan them.
"""

from django.db import migrations


def move_to_private(apps, schema_editor):
    from apps.common.storage import relocate_to_private

    model = apps.get_model("cv", "PortfolioItem")
    relocate_to_private(model, "file")
    relocate_to_private(model, "cover")


def move_back(apps, schema_editor):
    from apps.common.storage import relocate_to_private

    model = apps.get_model("cv", "PortfolioItem")
    relocate_to_private(model, "file", reverse=True)
    relocate_to_private(model, "cover", reverse=True)


class Migration(migrations.Migration):
    dependencies = [
        ("cv", "0003_alter_portfolioitem_cover_alter_portfolioitem_file"),
    ]

    operations = [migrations.RunPython(move_to_private, move_back)]
