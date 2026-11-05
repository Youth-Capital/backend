"""Connection configuration tests; never connect to a database."""
import json
import os
import subprocess
import sys

import pytest


def load_settings(values):
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("DB_", "POSTGRES_", "DATABASE_"))}
    environment.update(values)
    return subprocess.run(
        [sys.executable, "-c", ("import json; from config.settings.base import DATABASES; "
         "print(json.dumps(DATABASES['default']))")],
        env=environment, capture_output=True, text=True, check=False,
    )


def test_explicit_database_settings_override_legacy_url():
    result = load_settings({
        "DB_HOST": "shared-postgres", "DB_PORT": "5544",
        "DB_NAME": "yoshlar_kapitali_db", "DB_USER": "yoshlar_kapitali_user",
        "POSTGRES_PASSWORD": "test-only-$#'password", "DATABASE_SSL_REQUIRE": "False",
        "DATABASE_URL": "postgresql://old:old@old-db/old",
    })
    assert result.returncode == 0, result.stderr
    database = json.loads(result.stdout)
    assert database["HOST"] == "shared-postgres"
    assert database["PORT"] == 5544
    assert database["NAME"] == "yoshlar_kapitali_db"
    assert database["USER"] == "yoshlar_kapitali_user"
    assert database["PASSWORD"] == "test-only-$#'password"
    assert database["OPTIONS"] == {}
    assert database["CONN_HEALTH_CHECKS"] is True


@pytest.mark.parametrize("values", [
    {"DATABASE_URL": "postgresql://legacy:password@legacy-db:5545/legacy"},
    {"POSTGRES_DB": "legacy", "POSTGRES_USER": "legacy", "POSTGRES_HOST": "legacy-db",
     "POSTGRES_PORT": "5545"},
])
def test_existing_database_configuration_still_works(values):
    result = load_settings(values)
    assert result.returncode == 0, result.stderr
    database = json.loads(result.stdout)
    assert database["NAME"] == "legacy"
    assert database["USER"] == "legacy"
    assert database["HOST"] == "legacy-db"
    assert str(database["PORT"]) == "5545"


def test_partial_explicit_settings_fail_instead_of_using_wrong_database():
    result = load_settings({"DB_HOST": "shared-postgres"})
    assert result.returncode != 0
    assert "Set the DB_NAME environment variable" in result.stderr
