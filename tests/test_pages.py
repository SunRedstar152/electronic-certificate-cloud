from pathlib import Path
import re
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from main import app


STATIC_DIR = Path(__file__).parents[1] / "static"


class DashboardStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.labels = {}

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids[attributes["id"]] = tag
        if tag == "label" and attributes.get("for"):
            self.labels[attributes["for"]] = True


@pytest.fixture(scope="module")
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/", "/login", "/dashboard"])
def test_pages_are_served_with_pkru_brand_and_utf8(client, path):
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
    assert "PKRU Electronic Certificate" in response.text
    assert '<meta charset="UTF-8">' in response.text
    assert "เน€เธ" not in response.text


@pytest.mark.parametrize(
    ("filename", "required_text"),
    [
        ("index.html", ("ตรวจสอบเกียรติบัตร", "ดาวน์โหลดใบเกียรติบัตร")),
        ("login.html", ("เข้าสู่ระบบ", "ชื่อผู้ใช้งาน")),
        (
            "dashboard.html",
            (
                "ภาพรวม",
                "หน่วยงาน",
                "บันทึกการใช้งาน",
                "แม่แบบ",
                "ออกเกียรติบัตรรายบุคคล",
                "ออกเกียรติบัตรแบบกลุ่ม",
                "รายการประมวลผล",
                "เกียรติบัตร",
            ),
        ),
    ],
)
def test_pages_expose_the_required_thai_user_flows(filename, required_text):
    page = (STATIC_DIR / filename).read_text(encoding="utf-8")

    for text in required_text:
        assert text in page


def test_pages_use_local_assets_and_accessible_landmarks():
    for filename in ("index.html", "login.html", "dashboard.html"):
        page = (STATIC_DIR / filename).read_text(encoding="utf-8")
        assert 'lang="th"' in page
        assert "<main" in page
        assert "https://fonts.googleapis.com" not in page
        assert "https://cdn." not in page


def test_admin_organization_page_exposes_personnel_controls_without_internal_codes(client):
    response = client.get("/dashboard")
    structure = DashboardStructure()
    structure.feed(response.text)

    assert response.status_code == 200
    assert "organization-code" not in structure.ids
    assert {
        "organization-selector",
        "organization-fields",
        "personnel-account-fields",
        "personnel-dialog",
        "personnel-organization-id",
        "personnel-table-body",
        "password-organization-id",
        "password-user-id",
    }.issubset(structure.ids)
    assert structure.ids["organization-selector"] == "select"
    assert structure.ids["personnel-dialog"] == "dialog"
    assert {"organization-selector", "organization-name", "organization-username", "organization-password"}.issubset(structure.labels)


def test_static_javascript_has_no_unsafe_html_assignment_or_mojibake():
    javascript = "\n".join(
        path.read_text(encoding="utf-8") for path in (STATIC_DIR / "js").glob("*.js")
    )

    assert not re.search(r"\.innerHTML\s*=", javascript)
    assert "เน€เธ" not in javascript
    assert "Coming Soon" not in javascript


def test_frontend_uses_actual_api_contracts():
    javascript = "\n".join(
        path.read_text(encoding="utf-8") for path in (STATIC_DIR / "js").glob("*.js")
    )

    for route in (
        "/api/public/certificates/",
        "/login",
        "/me",
        "/admin/stats",
        "/admin/organizations",
        "/admin/audit-logs",
        "/templates",
        "/certificates",
        "/batches/preview",
        "/batches",
    ):
        assert route in javascript


def test_batch_ui_uses_the_backend_partial_status_value():
    dashboard = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert 'value="partial"' in dashboard
    assert "partial:" in javascript
    assert "completed_with_errors" not in dashboard + javascript


def test_visual_system_includes_accessibility_and_responsive_contracts():
    styles = "\n".join(
        path.read_text(encoding="utf-8") for path in (STATIC_DIR / "css").glob("*.css")
    )

    assert "--orange-500: #f97316" in styles
    assert ":focus-visible" in styles
    assert "prefers-reduced-motion: reduce" in styles
    assert "@media (max-width:" in styles


def test_template_editor_uses_drag_and_drop_positioning():
    dashboard = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert 'id="template-element-layer"' in dashboard
    assert 'type="range"' not in dashboard
    assert "pointerdown" in javascript
    assert "setPointerCapture" in javascript
    assert "moveTemplateMarkerWithKeyboard" in javascript
    assert 'id="template-real-preview"' in dashboard
    assert 'id="template-element-list"' in dashboard
    assert "/templates/preview" in javascript
    assert "AbortController" in javascript


def test_template_editor_supports_dynamic_fields_and_font_library():
    dashboard = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    for element_id in (
        "template-new-kind",
        "template-add-element",
        "property-font-size",
        "property-color",
        "property-weight",
        "property-align",
        "property-line-height",
        "property-width",
        "property-height",
        "font-upload-file",
    ):
        assert f'id="{element_id}"' in dashboard
    assert 'window.api.request("/fonts")' in javascript
    assert 'window.api.request("/fonts", { method: "POST"' in javascript
    assert "FONT_IN_USE" in javascript


def test_template_editor_supports_signature_blocks():
    dashboard = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert 'value="signature"' in dashboard
    assert "property-required" in javascript
    assert "property-align" in javascript
    assert "templatePreviewValues" in javascript


def test_signature_editor_is_available_to_single_bulk_and_history_flows():
    page = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "css" / "dashboard.css").read_text(encoding="utf-8")

    for element_id in (
        "signature-editor-template", "single-signature-host",
        "bulk-signature-host", "signature-request-dialog", "dialog-signature-host",
    ):
        assert f'id="{element_id}"' in page
    assert 'accept="image/png,.png"' in page
    assert 'data-signature-role="canvas"' in page
    assert 'touch-action: none' in styles
    assert 'window.SignaturePad.mount' in script
    assert 'payload.signatures = state.signatureEditors.single.submission()' in script
    assert 'body: { signatures:' in script
    signature_script = (STATIC_DIR / "js" / "signature-pad.js").read_text(encoding="utf-8")
    assert 'event.key === "ArrowRight"' in signature_script
    assert 'aria-controls' in signature_script
    assert "localStorage" not in signature_script
    assert "sessionStorage" not in signature_script


def test_mobile_navigation_supports_keyboard_dismissal_and_focus_return():
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert 'event.key === "Escape"' in javascript
    assert '$("#open-navigation").focus()' in javascript


def test_bulk_review_retains_backend_length_validation_and_individual_downloads():
    dashboard = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert "IMPORT_VALUE_TOO_LONG" in javascript
    assert "bulk-individual-downloads" in dashboard
    assert "renderBulkIndividualDownloads" in javascript


def test_successful_bulk_preview_clears_a_previous_technical_error():
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")
    preview_success = javascript.index(
        'const result = await window.api.request("/batches/preview"'
    )
    render_rows = javascript.index("renderBulkRows();", preview_success)

    assert "hideGlobalError();" in javascript[preview_success:render_rows]


def test_certificate_loader_reaches_records_after_the_first_api_page():
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    # The browser regression verifies real page selection and stale responses.
    import shutil
    import subprocess
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("Node.js is required for dashboard behavior checks")
    subprocess.run([executable, "--test", str(STATIC_DIR.parent / "tests/js/dashboard-pagination.test.js")], check=True, capture_output=True)


def test_closed_mobile_navigation_is_removed_from_keyboard_navigation():
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert "sidebar.inert = true" in javascript
    assert "sidebar.inert = false" in javascript
    assert 'sidebar.setAttribute("aria-hidden", "true")' in javascript


def test_single_issuance_reuses_its_idempotency_key_until_a_new_attempt_is_started():
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert "singleIssuanceAttempt" in javascript
    assert 'headers: { "Idempotency-Key": attempt.key }' in javascript
    assert "function resetSingleIssuanceAttempt()" in javascript
    assert '$("#single-form").addEventListener("reset", resetSingleIssuanceAttempt)' in javascript
    assert "renderSingleTemplateFields" in javascript


def test_single_issuance_uses_template_fields_without_live_preview_or_reference():
    page = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert 'id="single-preview"' not in page
    assert 'id="single-reference"' not in page
    assert 'id="single-custom-fields"' in page
    assert "LIVE PREVIEW" not in page
    assert "updateSinglePreview" not in script
    payload = script[script.index("function singleIssuancePayload"):script.index("function singleIssuanceAttempt")]
    assert "recipient_reference:" not in payload


def test_bulk_review_can_correct_all_custom_fields_without_unsafe_html_rendering():
    dashboard = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert "ข้อมูลเพิ่มเติม" in dashboard
    assert "function renderCustomFieldInputs(" in javascript
    assert "state.bulkRows[index].custom_fields[field] = input.value" in javascript
    assert "state.bulkRows[index].custom_fields[field][endpoint] = input.value" in javascript
    assert 'input.maxLength = 2000' in javascript
    assert ".innerHTML" not in javascript


def test_bulk_mapping_controls_are_derived_from_selected_template():
    dashboard = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert 'id="bulk-mapping-table-container"' in dashboard
    assert 'id="mapping-custom"' not in dashboard
    assert "function renderBulkMappingTable(" in javascript
    assert "window.TemplateLayout.mappingFields" in javascript
    assert 'select.dataset.columnName = header' in javascript


def test_public_result_scroll_respects_reduced_motion_preference():
    javascript = (STATIC_DIR / "js" / "main.js").read_text(encoding="utf-8")

    assert 'window.matchMedia("(prefers-reduced-motion: reduce)").matches' in javascript


def test_public_verification_displays_the_active_certificate_image():
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC_DIR / "js" / "main.js").read_text(encoding="utf-8")

    assert 'id="certificate-preview-image"' in page
    assert "/api/public/certificates/${encodedCode}/image" in javascript
    assert 'previewImage.addEventListener("load"' in javascript
    assert 'previewImage.addEventListener("error"' in javascript
    assert 'id="download-pdf"' in page
    assert 'id="download-jpg"' in page
    assert 'id="download-png"' in page
    assert "download?format=pdf" in javascript
    assert "download?format=jpg" in javascript
    assert "download?format=png" in javascript
    assert "fallbackSrc" in javascript


def test_pages_cache_bust_frontend_assets():
    for filename in ("index.html", "login.html"):
        page = (STATIC_DIR / filename).read_text(encoding="utf-8")
        assert "?v=20260813-5" in page
    dashboard = (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")
    assert "?v=20260823-1" in dashboard


def test_static_mount_does_not_publish_tenant_uploads(client):
    secret_dir = STATIC_DIR / "uploads" / "templates"
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret = secret_dir / "static-route-must-not-serve-this.txt"
    secret.write_text("tenant secret", encoding="utf-8")
    try:
        response = client.get(
            "/static/uploads/templates/static-route-must-not-serve-this.txt"
        )
    finally:
        secret.unlink(missing_ok=True)

    assert response.status_code == 404


def test_dashboard_uses_scoped_template_background_endpoint():
    javascript = (STATIC_DIR / "js" / "dashboard.js").read_text(encoding="utf-8")

    assert "`/templates/${template.id}/background`" in javascript
    assert "assetUrl(template.background_image_path)" not in javascript
