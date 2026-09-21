import re

from services.organization_identity import (
    generate_organization_code,
    normalize_organization_name,
)


def test_normalize_organization_name_uses_nfc_and_collapses_whitespace():
    decomposed = "ศูนย์วิจัย Cafe\u0301"

    assert normalize_organization_name(f"  {decomposed}\t\n  ภูเก็ต  ") == (
        "ศูนย์วิจัย Café ภูเก็ต"
    )


def test_generate_organization_code_has_required_format_for_repeated_values():
    values = [generate_organization_code() for _ in range(100)]

    assert all(re.fullmatch(r"ORG[A-Z0-9]{13}", value) for value in values)
