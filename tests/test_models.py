from datetime import date

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError

from models import (
    Activity,
    ActivityApprovalLog,
    ActivityStatus,
    Batch,
    BatchRow,
    Certificate,
    CertificateRecipientType,
    CertificateType,
    FontAsset,
    Organization,
    RecordStatus,
    Template,
    User,
    UserRole,
)
from schemas import (
    CertificateTypeCreate,
    CertificateTypeResponse,
    ErrorResponse,
    OrganizationResponse,
    UserCreate,
    UserResponse,
)


def test_organization_username_and_template_are_scoped(db_session):
    org = Organization(name="IT Center", code="ITC")
    user = User(
        username="itc",
        role=UserRole.organization,
        organization=org,
        password_hash="hash",
    )
    template = Template(
        name="Training",
        organization=org,
        background_image_path="x.png",
        positions={},
    )

    db_session.add_all([org, user, template])
    db_session.flush()

    assert user.organization_id == org.id == template.organization_id


@pytest.fixture
def batch(db_session):
    organization = Organization(name="Information Technology Center", code="ITC")
    creator = User(
        username="itc-batch-owner",
        password_hash="hash",
        role=UserRole.organization,
        organization=organization,
    )
    template = Template(
        name="Training",
        organization=organization,
        background_image_path="training.png",
        positions={},
    )
    value = Batch(
        organization=organization,
        template=template,
        topic="Security Awareness",
        issue_date=date(2026, 8, 12),
        source_type="single",
        created_by=creator,
    )
    db_session.add(value)
    db_session.flush()
    return value


def certificate_values(batch, recipient_reference, verification_code="PKRU-0001"):
    return {
        "organization": batch.organization,
        "batch": batch,
        "template": batch.template,
        "verification_code": verification_code,
        "recipient_reference": recipient_reference,
        "recipient_name": "Somchai Jaidee",
        "topic": batch.topic,
        "issue_date": batch.issue_date,
        "created_by": batch.created_by,
    }


def test_duplicate_recipient_reference_in_batch_is_rejected(db_session, batch):
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add_all(
                [
                    Certificate(**certificate_values(batch, "EMP-001", "PKRU-0001")),
                    Certificate(**certificate_values(batch, "EMP-001", "PKRU-0002")),
                ]
            )
            db_session.flush()


def test_organization_code_is_unique(db_session):
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add_all(
                [
                    Organization(name="First", code="DUP"),
                    Organization(name="Second", code="DUP"),
                ]
            )
            db_session.flush()


def test_certificate_verification_code_is_globally_unique(db_session, batch):
    other_batch = Batch(
        organization=batch.organization,
        template=batch.template,
        topic=batch.topic,
        issue_date=batch.issue_date,
        source_type="single",
        created_by=batch.created_by,
    )
    db_session.add(other_batch)
    db_session.flush()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add_all(
                [
                    Certificate(**certificate_values(batch, "EMP-001", "PKRU-SAME")),
                    Certificate(
                        **certificate_values(other_batch, "EMP-002", "PKRU-SAME")
                    ),
                ]
            )
            db_session.flush()


def test_structured_columns_use_postgresql_jsonb():
    assert isinstance(inspect(Template).columns.positions.type, JSONB)
    assert isinstance(inspect(Template).columns.layout.type, JSONB)
    assert inspect(Template).columns.layout.nullable is False
    assert isinstance(inspect(Certificate).columns.custom_fields.type, JSONB)


def test_font_asset_checksum_is_unique_within_an_organization(db_session):
    organization = Organization(name="Font Organization", code="FONT-ORG")
    db_session.add(organization)
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add_all([
                FontAsset(
                    organization_id=organization.id,
                    display_name="First Font",
                    original_filename="first.ttf",
                    file_path="fonts/first.ttf",
                    sha256="a" * 64,
                    format="ttf",
                    supports_thai=True,
                    status=RecordStatus.active,
                ),
                FontAsset(
                    organization_id=organization.id,
                    display_name="Duplicate Font",
                    original_filename="duplicate.ttf",
                    file_path="fonts/duplicate.ttf",
                    sha256="a" * 64,
                    format="ttf",
                    supports_thai=True,
                    status=RecordStatus.active,
                ),
            ])
            db_session.flush()


def test_font_asset_checksum_can_repeat_in_another_organization(db_session):
    first = Organization(name="First Font Organization", code="FONT-A")
    second = Organization(name="Second Font Organization", code="FONT-B")
    db_session.add_all([first, second])
    db_session.flush()
    db_session.add_all([
        FontAsset(
            organization_id=organization.id,
            display_name=f"Font {organization.code}",
            original_filename="font.ttf",
            file_path=f"fonts/{organization.code}.ttf",
            sha256="b" * 64,
            format="ttf",
            supports_thai=True,
            status=RecordStatus.active,
        )
        for organization in (first, second)
    ])

    db_session.flush()


def test_timestamp_columns_are_timezone_aware():
    for model in (Organization, User, Template, Batch, Certificate):
        assert inspect(model).columns.created_at.type.timezone is True


def test_schema_responses_support_models_without_exposing_passwords():
    organization = Organization(
        name="IT Center", code="ITC", status=RecordStatus.active
    )
    organization.id = 7
    response = OrganizationResponse.model_validate(organization)

    assert response.id == 7
    assert response.model_dump() == {
        "name": "IT Center",
        "contact_name": None,
        "contact_email": None,
        "contact_phone": None,
        "id": 7,
        "status": RecordStatus.active,
        "created_at": None,
        "updated_at": None,
    }
    assert "code" not in response.model_dump()
    assert "password" not in UserResponse.model_fields
    assert "password_hash" not in UserResponse.model_fields
    assert "password" in UserCreate.model_fields


def test_error_response_has_nested_stable_contract():
    response = ErrorResponse(
        error={
            "code": "AUTH_FORBIDDEN",
            "message": "You do not have permission to perform this action.",
            "correlation_id": "req-123",
        }
    )

    assert response.model_dump() == {
        "error": {
            "code": "AUTH_FORBIDDEN",
            "message": "You do not have permission to perform this action.",
            "correlation_id": "req-123",
        }
    }


@pytest.fixture
def ownership_graph(db_session):
    organization_a = Organization(name="Organization A", code="ORG-A")
    organization_b = Organization(name="Organization B", code="ORG-B")
    user_a = User(
        username="organization-a",
        password_hash="hash",
        role=UserRole.organization,
        organization=organization_a,
    )
    user_b = User(
        username="organization-b",
        password_hash="hash",
        role=UserRole.organization,
        organization=organization_b,
    )
    template_a = Template(
        name="Template A",
        organization=organization_a,
        background_image_path="a.png",
        positions={},
    )
    template_b = Template(
        name="Template B",
        organization=organization_b,
        background_image_path="b.png",
        positions={},
    )
    db_session.add_all(
        [organization_a, organization_b, user_a, user_b, template_a, template_b]
    )
    db_session.flush()
    return {
        "organization_a": organization_a,
        "organization_b": organization_b,
        "user_a": user_a,
        "user_b": user_b,
        "template_a": template_a,
        "template_b": template_b,
    }


def batch_values(graph, *, organization, template, creator):
    return {
        "organization_id": organization.id,
        "template_id": template.id,
        "topic": "Organization Isolation",
        "issue_date": date(2026, 8, 12),
        "source_type": "single",
        "created_by_id": creator.id,
    }


def test_batch_rejects_template_from_another_organization(
    db_session, ownership_graph
):
    graph = ownership_graph
    mismatched_batch = Batch(
        **batch_values(
            graph,
            organization=graph["organization_a"],
            template=graph["template_b"],
            creator=graph["user_a"],
        )
    )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(mismatched_batch)
            db_session.flush()


def test_batch_rejects_creator_from_another_organization(
    db_session, ownership_graph
):
    graph = ownership_graph
    mismatched_batch = Batch(
        **batch_values(
            graph,
            organization=graph["organization_a"],
            template=graph["template_a"],
            creator=graph["user_b"],
        )
    )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(mismatched_batch)
            db_session.flush()


@pytest.fixture
def organization_batches(db_session, ownership_graph):
    graph = ownership_graph
    batch_a = Batch(
        **batch_values(
            graph,
            organization=graph["organization_a"],
            template=graph["template_a"],
            creator=graph["user_a"],
        )
    )
    batch_b = Batch(
        **batch_values(
            graph,
            organization=graph["organization_b"],
            template=graph["template_b"],
            creator=graph["user_b"],
        )
    )
    db_session.add_all([batch_a, batch_b])
    db_session.flush()
    return graph, batch_a, batch_b


def explicit_certificate_values(
    *, organization, batch, template, creator, reference, code
):
    return {
        "organization_id": organization.id,
        "batch_id": batch.id,
        "template_id": template.id,
        "verification_code": code,
        "recipient_reference": reference,
        "recipient_name": "Recipient",
        "topic": "Organization Isolation",
        "issue_date": date(2026, 8, 12),
        "created_by_id": creator.id,
    }


def test_certificate_rejects_batch_from_another_organization(
    db_session, organization_batches
):
    graph, _, batch_b = organization_batches
    certificate = Certificate(
        **explicit_certificate_values(
            organization=graph["organization_a"],
            batch=batch_b,
            template=graph["template_a"],
            creator=graph["user_a"],
            reference="A-001",
            code="PKRU-A-001",
        )
    )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(certificate)
            db_session.flush()


def test_certificate_rejects_template_from_another_organization(
    db_session, organization_batches
):
    graph, batch_a, _ = organization_batches
    certificate = Certificate(
        **explicit_certificate_values(
            organization=graph["organization_a"],
            batch=batch_a,
            template=graph["template_b"],
            creator=graph["user_a"],
            reference="A-002",
            code="PKRU-A-002",
        )
    )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(certificate)
            db_session.flush()


def test_certificate_rejects_creator_from_another_organization(
    db_session, organization_batches
):
    graph, batch_a, _ = organization_batches
    certificate = Certificate(
        **explicit_certificate_values(
            organization=graph["organization_a"],
            batch=batch_a,
            template=graph["template_a"],
            creator=graph["user_b"],
            reference="A-003",
            code="PKRU-A-003",
        )
    )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(certificate)
            db_session.flush()


def test_certificate_rejects_revoker_from_another_organization(
    db_session, organization_batches
):
    graph, batch_a, _ = organization_batches
    values = explicit_certificate_values(
        organization=graph["organization_a"],
        batch=batch_a,
        template=graph["template_a"],
        creator=graph["user_a"],
        reference="A-004",
        code="PKRU-A-004",
    )
    certificate = Certificate(**values, revoked_by_id=graph["user_b"].id)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(certificate)
            db_session.flush()


def test_batch_row_rejects_certificate_from_another_batch(
    db_session, organization_batches
):
    graph, batch_a, batch_b = organization_batches
    certificate_b = Certificate(
        **explicit_certificate_values(
            organization=graph["organization_b"],
            batch=batch_b,
            template=graph["template_b"],
            creator=graph["user_b"],
            reference="B-001",
            code="PKRU-B-001",
        )
    )
    db_session.add(certificate_b)
    db_session.flush()
    mismatched_row = BatchRow(
        batch_id=batch_a.id,
        row_number=1,
        certificate_id=certificate_b.id,
    )

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(mismatched_row)
            db_session.flush()


def test_user_create_accepts_admin_without_organization():
    value = UserCreate(
        username="administrator",
        password="StrongPass123!",
        role=UserRole.admin,
        organization_id=None,
    )

    assert value.organization_id is None


def test_user_create_accepts_organization_user_with_organization():
    value = UserCreate(
        username="organization-user",
        password="StrongPass123!",
        role=UserRole.organization,
        organization_id=7,
    )

    assert value.organization_id == 7


@pytest.mark.parametrize(
    ("role", "organization_id"),
    [
        (UserRole.admin, 7),
        (UserRole.organization, None),
    ],
)
def test_user_create_rejects_role_organization_mismatch(role, organization_id):
    with pytest.raises(ValidationError):
        UserCreate(
            username="invalid-user",
            password="StrongPass123!",
            role=role,
            organization_id=organization_id,
        )


def test_activity_has_approval_status_and_history(db_session):
    organization = Organization(name="Activity Organization", code="ACT")
    proposer = User(
        username="activity-proposer",
        password_hash="hash",
        role=UserRole.organization,
        organization=organization,
    )
    activity = Activity(
        organization=organization,
        activity_name="Thai Workshop",
        description="Workshop description",
        activity_date=date(2026, 8, 28),
        activity_type="อบรม",
        proposed_by=proposer,
        approval_status=ActivityStatus.PENDING_APPROVAL,
    )
    db_session.add(activity)
    db_session.flush()
    approval = ActivityApprovalLog(
        activity=activity,
        organization_id=organization.id,
        decision=ActivityStatus.APPROVED,
        decided_by_id=proposer.id,
        comment="Approved",
    )
    db_session.add(approval)
    db_session.flush()

    assert activity.department_id == organization.id
    assert activity.approval_status is ActivityStatus.PENDING_APPROVAL
    assert approval.decision is ActivityStatus.APPROVED
    assert approval.comment == "Approved"


def test_certificate_type_and_recipient_type_schemas_validate():
    certificate_type = CertificateTypeCreate(
        name="ผ่านการอบรม", description="Training", status="active"
    )
    response = CertificateTypeResponse.model_validate(
        {"id": 1, **certificate_type.model_dump()}
    )

    assert response.name == "ผ่านการอบรม"
    assert CertificateRecipientType.EXTERNAL.value == "EXTERNAL"
