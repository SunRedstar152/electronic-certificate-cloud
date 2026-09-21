"""enforce unique normalized organization names

Revision ID: a7c1e9f4b2d8
Revises: 98f31a2c7d44
Create Date: 2026-08-23
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping

from alembic import op
import regex
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a7c1e9f4b2d8"
down_revision = "98f31a2c7d44"
branch_labels = None
depends_on = None


def _normalize_name(value: str) -> str:
    return regex.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def _normalized_rows(rows: Iterable[Mapping[str, object]]) -> list[tuple[int, str]]:
    normalized_rows: list[tuple[int, str]] = []
    seen: dict[str, int] = {}

    for row in rows:
        organization_id = int(row["id"])
        normalized_name = _normalize_name(str(row["name"]))
        key = normalized_name.casefold()
        previous_id = seen.get(key)
        if previous_id is not None:
            raise RuntimeError(
                "Duplicate normalized organization name for "
                f"organization ids {previous_id} and {organization_id}: "
                f"{normalized_name!r}"
            )
        seen[key] = organization_id
        normalized_rows.append((organization_id, normalized_name))

    return normalized_rows


def upgrade() -> None:
    organizations = sa.table(
        "organizations",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String(length=255)),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(organizations.c.id, organizations.c.name).order_by(organizations.c.id)
    ).mappings().all()

    normalized_rows = _normalized_rows(rows)
    for row, (organization_id, normalized_name) in zip(
        rows, normalized_rows, strict=True
    ):
        if str(row["name"]) == normalized_name:
            continue
        connection.execute(
            organizations.update()
            .where(organizations.c.id == organization_id)
            .values(name=normalized_name)
        )

    op.create_index(
        "uq_organizations_name_ci",
        "organizations",
        [sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_organizations_name_ci", table_name="organizations")
