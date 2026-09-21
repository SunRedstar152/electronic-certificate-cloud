from __future__ import annotations

import secrets
import string
import unicodedata

import regex


ORGANIZATION_CODE_PREFIX = "ORG"
ORGANIZATION_CODE_LENGTH = 16
_RANDOM_LENGTH = ORGANIZATION_CODE_LENGTH - len(ORGANIZATION_CODE_PREFIX)
_ALPHABET = string.ascii_uppercase + string.digits


def normalize_organization_name(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    return regex.sub(r"\s+", " ", normalized).strip()


def generate_organization_code() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(_RANDOM_LENGTH))
    return f"{ORGANIZATION_CODE_PREFIX}{suffix}"
