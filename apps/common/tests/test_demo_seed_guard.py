"""Demo seeding must be impossible against a production database.

`seed_demo` creates accounts whose password is a constant in this public
repository — and a superuser with it when the database has no administrator.
`seed_billing_demo` marks subscriptions paid and writes payment rows no money
backed. On a server, either one is an incident.

The refusal is inside the command rather than in a deployment note, because
the operator who needs the note is the one who will not be reading it. These
tests pin both halves: it refuses where it must, and it still works where the
demo is supposed to run.
"""

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.accounts.models import User
from apps.billing.models import Payment, Plan
from apps.common.enums import Role

DEMO_COMMANDS = ["seed_demo", "seed_billing_demo"]

#: How a deployed server looks from inside the process: DEBUG off, and none of
#: the test suite's own flags set.
SERVER = {"DEBUG": False, "TESTING": False}

#: A development machine: DEBUG on, not the test suite.
DEVELOPMENT = {"DEBUG": True, "TESTING": False}


# ---------------------------------------------------------------------------
# It refuses on a server
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("command", DEMO_COMMANDS)
def test_a_server_refuses_to_seed_demo_data(command):
    """No `django_db` mark here, and that is the interesting half.

    pytest-django lets a test without that mark reach no database at all: any
    connection raises RuntimeError instead — including the one that
    `transaction.atomic` opens on its way into the command, which is how the
    first version of this guard went wrong. It refused *after* connecting to
    the production database and opening a transaction on it. So this test
    passing says the refusal happens before anything reaches for the database,
    which is what "must not touch production" has to mean.
    """
    with override_settings(**SERVER), pytest.raises(CommandError):
        call_command(command)


@pytest.mark.parametrize("command", DEMO_COMMANDS)
def test_the_refusal_explains_that_demo_seeding_is_not_allowed_in_production(command):
    with override_settings(**SERVER), pytest.raises(CommandError) as refusal:
        call_command(command)

    message = str(refusal.value)
    assert "Demo seeding is not allowed in production" in message
    assert command in message  # which command was refused
    assert "DEBUG is off" in message  # and why


@pytest.mark.parametrize("command", DEMO_COMMANDS)
def test_production_settings_are_refused_even_with_debug_somehow_on(command):
    """The settings module alone is enough, whatever else the process says."""
    with override_settings(SETTINGS_MODULE="config.settings.prod", DEBUG=True, TESTING=True):
        with pytest.raises(CommandError) as refusal:
            call_command(command)

    assert "config.settings.prod" in str(refusal.value)


@pytest.mark.django_db
def test_the_refusal_comes_before_any_work():
    """Not a check the command reaches eventually — the first thing it does.

    `seed_billing_demo` returns early when there are no plans, so a guard
    placed after that check would look like it worked while doing nothing.
    Seed the plans first: the command now has everything it needs, and must
    still refuse without writing a single payment.
    """
    call_command("seed_plans", stdout=StringIO())
    assert Plan.objects.exists()

    with override_settings(**SERVER), pytest.raises(CommandError):
        call_command("seed_billing_demo")

    assert Payment.objects.count() == 0


@pytest.mark.django_db
def test_nothing_is_created_when_seed_demo_refuses():
    before = User.objects.count()

    with override_settings(**SERVER), pytest.raises(CommandError):
        call_command("seed_demo")

    assert User.objects.count() == before
    assert not User.objects.filter(email__endswith="@demo.uz").exists()


@pytest.mark.django_db
def test_nothing_is_deleted_when_seed_demo_refuses_with_wipe():
    """`--wipe` deletes every @demo.uz account. It must not reach that line."""
    User.objects.create_user(
        email="someone@demo.uz", password="not-the-demo-password-9x", role=Role.STUDENT
    )

    with override_settings(**SERVER), pytest.raises(CommandError):
        call_command("seed_demo", "--wipe")

    assert User.objects.filter(email="someone@demo.uz").exists()


# ---------------------------------------------------------------------------
# It still runs where the demo belongs
# ---------------------------------------------------------------------------
@pytest.mark.django_db
def test_a_development_machine_may_seed():
    """DEBUG on: the guard stands aside and the command does its own work.

    With no plans in the database `seed_billing_demo` says so and stops — the
    point is that it got far enough to say anything at all.
    """
    out = StringIO()

    with override_settings(**DEVELOPMENT):
        call_command("seed_billing_demo", stdout=out)

    assert "No plans found" in out.getvalue()


@pytest.mark.django_db
def test_the_test_suite_may_seed():
    """This suite runs with DEBUG off and is allowed anyway, by TESTING."""
    out = StringIO()

    call_command("seed_billing_demo", stdout=out)

    assert "No plans found" in out.getvalue()


@pytest.mark.django_db
def test_seed_demo_still_creates_the_demo_accounts():
    call_command("seed_taxonomy", stdout=StringIO())

    call_command("seed_demo", stdout=StringIO(), stderr=StringIO())

    demo = User.objects.filter(email__endswith="@demo.uz")
    assert demo.count() >= 3
    assert demo.filter(role=Role.EMPLOYER).exists()
    assert demo.filter(role=Role.STUDENT).exists()


@pytest.mark.django_db
def test_seed_billing_demo_still_assigns_plans():
    call_command("seed_taxonomy", stdout=StringIO())
    call_command("seed_demo", stdout=StringIO(), stderr=StringIO())
    call_command("seed_plans", stdout=StringIO())

    call_command("seed_billing_demo", stdout=StringIO())

    assert Payment.objects.filter(status="SUCCEEDED").exists()


# ---------------------------------------------------------------------------
# The commands that are meant for production are untouched
# ---------------------------------------------------------------------------
@pytest.mark.django_db
@pytest.mark.parametrize("command", ["seed_taxonomy", "seed_plans"])
def test_real_reference_data_still_seeds_on_a_server(command):
    """Taxonomy and plans are production data — a deploy runs them."""
    with override_settings(**SERVER):
        call_command(command, stdout=StringIO())
