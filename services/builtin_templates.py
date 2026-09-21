"""The original design is shipped with the application, independent of its data."""

from copy import deepcopy
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from models import Organization, RecordStatus, Template
from services.template_layout import normalize_layout


BUILTIN_KEY = "original"
BUILTIN_IMAGE_REFERENCE = "builtin:original"
ASSET_DIRECTORY = Path(__file__).resolve().parents[1] / "assets" / "templates" / "original"


def original_definition() -> dict:
    definition = json.loads((ASSET_DIRECTORY / "template.json").read_text(encoding="utf-8"))
    if definition["key"] != BUILTIN_KEY or definition["background"] != "background.webp":
        raise RuntimeError("The bundled original template is invalid.")
    if not (ASSET_DIRECTORY / "background.webp").is_file():
        raise RuntimeError("The bundled original template background is missing.")
    definition["layout"] = normalize_layout(definition["layout"])
    return definition


def ensure_original_template(db: Session, organization_id: int) -> Template:
    """Restore the protected template inside the caller's transaction."""
    definition = original_definition()
    values = {
        "name": definition["name"],
        "background_image_path": BUILTIN_IMAGE_REFERENCE,
        "positions": deepcopy(definition["positions"]),
        "dimensions": deepcopy(definition["dimensions"]),
        "layout": deepcopy(definition["layout"]),
        "status": RecordStatus.active,
        "deleted_at": None,
    }
    statement = select(Template).where(
        Template.organization_id == organization_id, Template.builtin_key == BUILTIN_KEY
    )
    template = db.scalar(statement)
    if template is None:
        db.execute(
            insert(Template).values(organization_id=organization_id, builtin_key=BUILTIN_KEY, **values)
            .on_conflict_do_nothing(constraint="uq_templates_organization_builtin")
        )
        template = db.scalar(statement)
    if template is None:
        raise RuntimeError("The original template could not be initialized.")
    for key, value in values.items():
        if getattr(template, key) != value:
            setattr(template, key, value)
    db.flush()
    return template


def ensure_all_original_templates(db: Session) -> None:
    for organization_id in db.scalars(select(Organization.id)):
        ensure_original_template(db, organization_id)
