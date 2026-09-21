from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from services.organization_identity import normalize_organization_name


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "a7c1e9f4b2d8_unique_organization_name.py"
)


@pytest.fixture
def migration_module():
    if not MIGRATION_PATH.exists():
        pytest.fail("the unique organization-name migration is not available")

    spec = importlib.util.spec_from_file_location(
        "unique_organization_name_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def apply_upgrade(migration_module, db_session) -> None:
    """Run this migration's PostgreSQL operations within the test transaction."""
    migration_context = MigrationContext.configure(db_session.connection())
    with Operations.context(migration_context):
        migration_module.upgrade()


@pytest.fixture
def migration_database(db_session):
    """A migration-sized PostgreSQL table that remains isolated from head state."""
    connection = db_session.connection()
    connection.execute(
        text(
            "CREATE TEMPORARY TABLE organizations ("
            "id integer PRIMARY KEY, "
            "name varchar(255) NOT NULL, "
            "code varchar(32) NOT NULL"
            ") ON COMMIT DROP"
        )
    )
    return connection


def test_migration_normalizes_existing_names_deterministically(migration_module):
    rows = migration_module._normalized_rows(
        [
            {"id": 1, "name": "  คณะวิทยาศาสตร์\tและเทคโนโลยี  "},
            {"id": 2, "name": "ศูนย์วิจัย Cafe\u0301"},
        ]
    )

    assert rows == [
        (1, "คณะวิทยาศาสตร์ และเทคโนโลยี"),
        (2, "ศูนย์วิจัย Café"),
    ]


def test_migration_preserves_interior_information_separator_like_task_one(
    migration_module,
):
    name = "  Faculty\u001cScience  "

    assert normalize_organization_name(name) == "Faculty\u001cScience"
    assert migration_module._normalized_rows([{"id": 1, "name": name}]) == [
        (1, "Faculty\u001cScience")
    ]


def test_migration_reports_both_ids_for_normalized_duplicates(migration_module):
    with pytest.raises(RuntimeError, match=r"organization ids 4 and 9"):
        migration_module._normalized_rows(
            [
                {"id": 4, "name": "ศูนย์ภาษา"},
                {"id": 9, "name": "  ศูนย์ภาษา  "},
            ]
        )


def test_migration_failure_leaves_existing_rows_unchanged(
    migration_module, db_session, migration_database
):
    migration_database.execute(
        text(
            "INSERT INTO organizations (id, name, code) VALUES "
            "(4, :first_name, :first_code), (9, :second_name, :second_code)"
        ),
        {
            "first_name": "  Language Center ",
            "first_code": "MIGRATION-FIRST",
            "second_name": "language   center",
            "second_code": "MIGRATION-SECOND",
        },
    )

    with pytest.raises(RuntimeError, match=r"organization ids 4 and 9"):
        apply_upgrade(migration_module, db_session)

    stored = migration_database.execute(
        text("SELECT id, name, code FROM organizations ORDER BY id")
    ).all()
    assert stored == [
        (4, "  Language Center ", "MIGRATION-FIRST"),
        (9, "language   center", "MIGRATION-SECOND"),
    ]


def test_migration_updates_only_names_changed_by_normalization(
    migration_module, db_session, migration_database
):
    migration_database.execute(
        text(
            "INSERT INTO organizations (id, name, code) VALUES "
            "(1, 'Faculty of Science', 'MIGRATION-NORMALIZED'), "
            "(2, '  Faculty   of Arts  ', 'MIGRATION-CHANGED')"
        )
    )
    migration_database.execute(
        text(
            "CREATE TEMPORARY TABLE organization_update_events "
            "(id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY) ON COMMIT DROP"
        )
    )
    migration_database.execute(
        text(
            "CREATE FUNCTION pg_temp.count_organization_name_updates() "
            "RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN "
            "INSERT INTO organization_update_events DEFAULT VALUES; "
            "RETURN NEW; "
            "END; "
            "$$"
        )
    )
    migration_database.execute(
        text(
            "CREATE TRIGGER count_organization_name_updates "
            "AFTER UPDATE OF name ON organizations "
            "FOR EACH ROW EXECUTE FUNCTION pg_temp.count_organization_name_updates()"
        )
    )

    apply_upgrade(migration_module, db_session)

    assert migration_database.scalar(
        text("SELECT count(*) FROM organization_update_events")
    ) == 1
    assert migration_database.execute(
        text("SELECT id, name FROM organizations ORDER BY id")
    ).all() == [(1, "Faculty of Science"), (2, "Faculty of Arts")]


def test_migrated_postgresql_index_rejects_case_equivalent_names(
    migration_module, db_session, migration_database
):
    migration_database.execute(
        text(
            "INSERT INTO organizations (id, name, code) "
            "VALUES (1, '  Faculty   of Science ', 'MIGRATION-SCI')"
        )
    )

    apply_upgrade(migration_module, db_session)
    assert migration_database.scalar(
        text("SELECT name FROM organizations WHERE id = 1")
    ) == "Faculty of Science"

    with pytest.raises(IntegrityError):
        with migration_database.begin_nested():
            migration_database.execute(
                text(
                    "INSERT INTO organizations (id, name, code) "
                    "VALUES (2, 'faculty OF science', 'MIGRATION-DUP')"
                )
            )

    migration_database.execute(
        text(
            "INSERT INTO organizations (id, name, code) "
            "VALUES (3, 'Faculty of Arts', 'MIGRATION-ART')"
        )
    )
    assert migration_database.scalar(text("SELECT count(*) FROM organizations")) == 2
