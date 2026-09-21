"""template layout and organization fonts

Revision ID: 98f31a2c7d44
Revises: cbef4c6ea711
Create Date: 2026-08-13 13:00:00

"""

from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "98f31a2c7d44"
down_revision: Union[str, Sequence[str], None] = "cbef4c6ea711"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dimensions(value: object) -> tuple[int, int]:
    if isinstance(value, dict):
        width = value.get("width")
        height = value.get("height")
        if (
            isinstance(width, int)
            and not isinstance(width, bool)
            and isinstance(height, int)
            and not isinstance(height, bool)
            and width > 0
            and height > 0
        ):
            return width, height
    return 1600, 1131


def _position(value: object) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    x = value.get("x")
    y = value.get("y")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
        or not 0.0 <= x <= 1.0
        or not 0.0 <= y <= 1.0
    ):
        return None
    return float(x), float(y)


def _box(
    center_x: float,
    center_y: float,
    width: float,
    height: float,
) -> dict[str, float]:
    width = min(1.0, max(0.001, width))
    height = min(1.0, max(0.001, height))
    return {
        "x": min(1.0 - width, max(0.0, center_x - width / 2)),
        "y": min(1.0 - height, max(0.0, center_y - height / 2)),
        "width": width,
        "height": height,
    }


def _text_element(
    element_id: str,
    source: str,
    label: str,
    kind: str,
    position: tuple[float, float],
    image_height: int,
    font_size: int,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "id": element_id,
        "source": source,
        "kind": kind,
        "label": label,
        "required": True,
        "visible": True,
        "box": _box(
            position[0],
            position[1],
            0.6,
            max(0.04, font_size * 1.4 / image_height),
        ),
        "style": {
            "font": {"source": "builtin", "key": "sarabun"},
            "font_size_px": font_size,
            "color": "#000000",
            "weight": 400,
            "align": "center",
            "line_height": 1.25,
        },
    }
    if kind == "date":
        value["format"] = {
            "locale": "th-TH",
            "calendar": "gregorian",
            "pattern": "numeric",
            "prefix": "",
            "separator": " ถึง ",
            "suffix": "",
        }
    return value


def _legacy_layout(positions: object, dimensions: object) -> dict[str, Any]:
    values = positions if isinstance(positions, dict) else {}
    width, height = _dimensions(dimensions)
    minimum = min(width, height)
    text_size = max(14, round(minimum * 0.055))
    date_size = max(12, round(text_size * 0.72))
    elements: list[dict[str, Any]] = []

    definitions = (
        ("recipient_name", ("recipient_name", "name"), "recipient_name", "ชื่อผู้รับ", "text", text_size),
        ("topic", ("topic",), "topic", "หัวข้อ / กิจกรรม", "text", text_size),
        ("issue_date", ("issue_date",), "issue_date", "วันที่ออก", "date", date_size),
    )
    for element_id, aliases, source, label, kind, font_size in definitions:
        raw = next((values[key] for key in aliases if key in values), None)
        position = _position(raw)
        if position is not None:
            elements.append(
                _text_element(
                    element_id,
                    source,
                    label,
                    kind,
                    position,
                    height,
                    font_size,
                )
            )

    raw_qr = next((values[key] for key in ("qr_code", "qr") if key in values), None)
    qr_position = _position(raw_qr)
    if qr_position is not None:
        qr_size = max(48, round(minimum * 0.24))
        elements.append({
            "id": "verification_qr",
            "source": "verification_url",
            "kind": "qr",
            "label": "คิวอาร์โค้ด",
            "required": True,
            "visible": True,
            "box": _box(
                qr_position[0],
                qr_position[1],
                qr_size / width,
                qr_size / height,
            ),
        })
    return {"version": 2, "elements": elements}


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column(
            "layout",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    record_status = postgresql.ENUM(
        "active",
        "inactive",
        name="record_status",
        create_type=False,
    )
    op.create_table(
        "font_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("supports_thai", sa.Boolean(), nullable=False),
        sa.Column("status", record_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            name="uq_font_assets_id_organization_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "sha256",
            name="uq_font_assets_organization_sha256",
        ),
    )
    op.create_index(
        op.f("ix_font_assets_organization_id"),
        "font_assets",
        ["organization_id"],
        unique=False,
    )

    templates = sa.table(
        "templates",
        sa.column("id", sa.Integer()),
        sa.column("positions", postgresql.JSONB()),
        sa.column("dimensions", postgresql.JSONB()),
        sa.column("layout", postgresql.JSONB()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(templates.c.id, templates.c.positions, templates.c.dimensions)
    ).mappings()
    for row in rows:
        connection.execute(
            templates.update()
            .where(templates.c.id == row["id"])
            .values(layout=_legacy_layout(row["positions"], row["dimensions"]))
        )
    null_count = connection.scalar(
        sa.select(sa.func.count()).select_from(templates).where(templates.c.layout.is_(None))
    )
    if null_count:
        raise RuntimeError("Template layout backfill did not complete.")
    op.alter_column("templates", "layout", existing_type=postgresql.JSONB(), nullable=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_font_assets_organization_id"), table_name="font_assets")
    op.drop_table("font_assets")
    op.drop_column("templates", "layout")
