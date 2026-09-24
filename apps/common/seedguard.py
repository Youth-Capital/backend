"""One rule, shared by every command that invents data: not on a server.

`seed_demo` creates accounts whose password is a constant in this repository,
and will create a superuser with it when the database has no administrator yet.
`seed_billing_demo` marks subscriptions paid and writes payment rows that no
money ever backed. Both are exactly what a demonstration needs and exactly what
a production database must never see.

Nothing about the command being *typed* can be trusted to prevent that: the
settings module is chosen by an environment variable, `manage.py` falls back to
development settings when that variable is missing, and a tired operator on the
wrong shell looks identical to a careful one. So the refusal lives here, inside
the command, and reads the settings the process actually booted with.

The rule, in order:

1. A settings module named for a deployment — prod, production, staging — is
   refused outright. This one is checked first and admits no exception: if the
   process booted a server's settings, nothing else about it matters.
2. `DEBUG` on means a development machine, and seeding is allowed.
3. The test suite runs with `DEBUG` off, so it says so explicitly by setting
   `TESTING`. That flag is set in `config/settings/test.py` and nowhere else —
   in particular no environment variable can turn it on.

Anything else is treated as a server and refused.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import CommandError

#: The last segment of a settings module that names a real deployment.
SERVER_SETTINGS_MODULES = frozenset({"prod", "production", "staging"})


def production_reason() -> str | None:
    """Why this process looks like a server, or None if it looks like a desk."""
    module = getattr(settings, "SETTINGS_MODULE", "") or ""
    if module.rsplit(".", 1)[-1].lower() in SERVER_SETTINGS_MODULES:
        return f"the settings module is {module}"
    if settings.DEBUG:
        return None
    if getattr(settings, "TESTING", False):
        return None
    return "DEBUG is off, which is how a deployed server runs"


def refuse_in_production(command: str) -> None:
    """Raise CommandError unless this is a development machine or the tests.

    Raising rather than returning a flag is deliberate: a command that forgets
    to check a return value would seed anyway, and the whole point is that the
    failure mode is refusing to run, never running quietly.
    """
    reason = production_reason()
    if reason is None:
        return

    raise CommandError(
        f"Demo seeding is not allowed in production. Refusing to run "
        f"`{command}`: {reason}.\n"
        "\n"
        # Plain ASCII on purpose: this text is printed to a terminal whose
        # encoding nobody controls, and a refusal is the worst place for a
        # character that might come out as a question mark.
        "This command writes demonstration data: accounts whose password is "
        "published in this repository, courses, vacancies, and subscriptions "
        "marked paid without any payment. Against a production database that "
        "is a security incident, not a mistake to undo.\n"
        "\n"
        "Run it on a development machine (DJANGO_SETTINGS_MODULE="
        "config.settings.dev, DEBUG on). Real reference data has its own "
        "commands, which are meant for production: seed_taxonomy, seed_plans, "
        "seed_soft_skill_test."
    )
