-- PKRU Electronic Certificate - PostgreSQL 18
-- MAIN SQL FILE: sql/schema.sql
-- Database: pkru_certificate (select/create it before importing).
-- For an EMPTY database only. Contains schema only, no users or demo data.
-- The Compose migrate service imports this file when the public schema is empty.
-- Existing databases: use the Compose migrate service; do not import this again.
-- Generated from models.py; schema revision: c8d9e0f1a2b3
-- Regenerate: python scripts/export_schema.py

BEGIN;
SET LOCAL search_path TO public;

CREATE TYPE record_status AS ENUM ('active', 'inactive');

CREATE TYPE user_role AS ENUM ('admin', 'organization');

CREATE TYPE activity_status AS ENUM ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'CANCELLED');

CREATE TYPE batch_status AS ENUM ('draft', 'validated', 'processing', 'completed', 'partial', 'failed');

CREATE TYPE certificate_recipient_type AS ENUM ('INTERNAL', 'EXTERNAL');

CREATE TYPE certificate_status AS ENUM ('active', 'revoked');

CREATE TABLE organizations (
	deleted_at TIMESTAMP WITH TIME ZONE,
	id SERIAL NOT NULL,
	name VARCHAR(255) NOT NULL,
	code VARCHAR(32) NOT NULL,
	contact_name VARCHAR(255),
	contact_email VARCHAR(320),
	contact_phone VARCHAR(50),
	status record_status NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_organizations_code UNIQUE (code)
);

CREATE UNIQUE INDEX uq_organizations_name_ci ON organizations (lower(name));

CREATE TABLE login_attempt_buckets (
	bucket_key VARCHAR(64) NOT NULL,
	failed_attempts INTEGER DEFAULT '0' NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (bucket_key),
	CONSTRAINT ck_login_attempt_buckets_failures CHECK (failed_attempts >= 0)
);

CREATE INDEX ix_login_attempt_buckets_expires_at ON login_attempt_buckets (expires_at);

CREATE TABLE users (
	deleted_at TIMESTAMP WITH TIME ZONE,
	id SERIAL NOT NULL,
	username VARCHAR(100) NOT NULL,
	password_hash VARCHAR(255) NOT NULL,
	role user_role NOT NULL,
	organization_id INTEGER,
	is_active BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_users_role_organization CHECK ((role = 'admin' AND organization_id IS NULL) OR (role = 'organization' AND organization_id IS NOT NULL)),
	CONSTRAINT uq_users_username UNIQUE (username),
	CONSTRAINT uq_users_id_organization_id UNIQUE (id, organization_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT
);

CREATE INDEX ix_users_username ON users (username);

CREATE TABLE templates (
	id SERIAL NOT NULL,
	organization_id INTEGER NOT NULL,
	name VARCHAR(255) NOT NULL,
	background_image_path VARCHAR(1024) NOT NULL,
	builtin_key VARCHAR(64),
	deleted_at TIMESTAMP WITH TIME ZONE,
	positions JSONB NOT NULL,
	dimensions JSONB NOT NULL,
	layout JSONB NOT NULL,
	status record_status NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_templates_id_organization_id UNIQUE (id, organization_id),
	CONSTRAINT uq_templates_organization_builtin UNIQUE (organization_id, builtin_key),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT
);

CREATE INDEX ix_templates_organization_id ON templates (organization_id);

CREATE TABLE font_assets (
	id SERIAL NOT NULL,
	organization_id INTEGER NOT NULL,
	display_name VARCHAR(255) NOT NULL,
	original_filename VARCHAR(255) NOT NULL,
	file_path VARCHAR(1024) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	format VARCHAR(8) NOT NULL,
	supports_thai BOOLEAN NOT NULL,
	status record_status NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_font_assets_organization_sha256 UNIQUE (organization_id, sha256),
	CONSTRAINT uq_font_assets_id_organization_id UNIQUE (id, organization_id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT
);

CREATE INDEX ix_font_assets_organization_id ON font_assets (organization_id);

CREATE TABLE certificate_types (
	id SERIAL NOT NULL,
	organization_id INTEGER,
	name VARCHAR(255) NOT NULL,
	description TEXT,
	status record_status NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_certificate_types_org_name UNIQUE (organization_id, name),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT
);

CREATE INDEX ix_certificate_types_organization_id ON certificate_types (organization_id);

CREATE TABLE activities (
	id SERIAL NOT NULL,
	organization_id INTEGER NOT NULL,
	department_id INTEGER NOT NULL,
	activity_name VARCHAR(255) NOT NULL,
	description TEXT,
	activity_date DATE NOT NULL,
	activity_type VARCHAR(100) NOT NULL,
	proposed_by_id INTEGER NOT NULL,
	approval_status activity_status NOT NULL,
	approved_by_id INTEGER,
	approved_at TIMESTAMP WITH TIME ZONE,
	approval_comment TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	FOREIGN KEY(department_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	FOREIGN KEY(proposed_by_id) REFERENCES users (id) ON DELETE RESTRICT,
	FOREIGN KEY(approved_by_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_activities_organization_id ON activities (organization_id);

CREATE INDEX ix_activities_organization_status ON activities (organization_id, approval_status);

CREATE INDEX ix_activities_approval_status ON activities (approval_status);

CREATE INDEX ix_activities_proposed_by_id ON activities (proposed_by_id);

CREATE INDEX ix_activities_department_id ON activities (department_id);

CREATE TABLE audit_logs (
	id SERIAL NOT NULL,
	actor_id INTEGER,
	organization_id INTEGER,
	action VARCHAR(100) NOT NULL,
	target_type VARCHAR(100) NOT NULL,
	target_id VARCHAR(255),
	correlation_id VARCHAR(100),
	details JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(actor_id) REFERENCES users (id) ON DELETE SET NULL,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_logs_action ON audit_logs (action);

CREATE INDEX ix_audit_logs_actor_id ON audit_logs (actor_id);

CREATE INDEX ix_audit_logs_correlation_id ON audit_logs (correlation_id);

CREATE INDEX ix_audit_logs_organization_id ON audit_logs (organization_id);

CREATE INDEX ix_audit_logs_created_at ON audit_logs (created_at);

CREATE TABLE activity_approval_logs (
	id SERIAL NOT NULL,
	activity_id INTEGER NOT NULL,
	organization_id INTEGER NOT NULL,
	decision activity_status NOT NULL,
	decided_by_id INTEGER NOT NULL,
	comment TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE CASCADE,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	FOREIGN KEY(decided_by_id) REFERENCES users (id) ON DELETE RESTRICT
);

CREATE INDEX ix_activity_approval_logs_activity_id ON activity_approval_logs (activity_id);

CREATE INDEX ix_activity_approval_logs_decided_by_id ON activity_approval_logs (decided_by_id);

CREATE INDEX ix_activity_approval_logs_organization_id ON activity_approval_logs (organization_id);

CREATE TABLE batches (
	id SERIAL NOT NULL,
	organization_id INTEGER NOT NULL,
	template_id INTEGER NOT NULL,
	topic VARCHAR(500) NOT NULL,
	issue_date DATE NOT NULL,
	event_metadata JSONB NOT NULL,
	source_type VARCHAR(32) NOT NULL,
	status batch_status NOT NULL,
	total_count INTEGER NOT NULL,
	success_count INTEGER NOT NULL,
	failure_count INTEGER NOT NULL,
	created_by_id INTEGER NOT NULL,
	activity_id INTEGER,
	certificate_type_id INTEGER,
	recipient_type certificate_recipient_type DEFAULT 'INTERNAL' NOT NULL,
	external_organization VARCHAR(255),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_batches_total_count CHECK (total_count >= 0),
	CONSTRAINT ck_batches_success_count CHECK (success_count >= 0),
	CONSTRAINT ck_batches_failure_count CHECK (failure_count >= 0),
	CONSTRAINT uq_batches_id_organization_id UNIQUE (id, organization_id),
	CONSTRAINT fk_batches_template_organization FOREIGN KEY(template_id, organization_id) REFERENCES templates (id, organization_id) ON DELETE RESTRICT,
	CONSTRAINT fk_batches_creator_organization FOREIGN KEY(created_by_id, organization_id) REFERENCES users (id, organization_id) ON DELETE RESTRICT,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	CONSTRAINT fk_batches_activity FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE RESTRICT,
	CONSTRAINT fk_batches_certificate_type FOREIGN KEY(certificate_type_id) REFERENCES certificate_types (id) ON DELETE RESTRICT
);

CREATE INDEX ix_batches_organization_id ON batches (organization_id);

CREATE INDEX ix_batches_certificate_type_id ON batches (certificate_type_id);

CREATE INDEX ix_batches_created_by_id ON batches (created_by_id);

CREATE INDEX ix_batches_activity_id ON batches (activity_id);

CREATE INDEX ix_batches_template_id ON batches (template_id);

CREATE TABLE certificates (
	id SERIAL NOT NULL,
	organization_id INTEGER NOT NULL,
	batch_id INTEGER,
	template_id INTEGER NOT NULL,
	verification_code VARCHAR(64) NOT NULL,
	recipient_reference VARCHAR(255) NOT NULL,
	recipient_name VARCHAR(255) NOT NULL,
	topic VARCHAR(500) NOT NULL,
	custom_fields JSONB NOT NULL,
	issue_date DATE NOT NULL,
	status certificate_status NOT NULL,
	revoked_at TIMESTAMP WITH TIME ZONE,
	revocation_reason TEXT,
	revoked_by_id INTEGER,
	image_path VARCHAR(1024),
	idempotency_key VARCHAR(255),
	created_by_id INTEGER NOT NULL,
	activity_id INTEGER,
	certificate_type_id INTEGER,
	recipient_type certificate_recipient_type DEFAULT 'INTERNAL' NOT NULL,
	external_organization VARCHAR(255),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_certificates_batch_recipient_reference UNIQUE (batch_id, recipient_reference),
	CONSTRAINT uq_certificates_verification_code UNIQUE (verification_code),
	CONSTRAINT uq_certificates_org_idempotency_key UNIQUE (organization_id, idempotency_key),
	CONSTRAINT uq_certificates_id_batch_id UNIQUE (id, batch_id),
	CONSTRAINT fk_certificates_batch_organization FOREIGN KEY(batch_id, organization_id) REFERENCES batches (id, organization_id) ON DELETE RESTRICT,
	CONSTRAINT fk_certificates_template_organization FOREIGN KEY(template_id, organization_id) REFERENCES templates (id, organization_id) ON DELETE RESTRICT,
	CONSTRAINT fk_certificates_creator_organization FOREIGN KEY(created_by_id, organization_id) REFERENCES users (id, organization_id) ON DELETE RESTRICT,
	CONSTRAINT fk_certificates_revoker_organization FOREIGN KEY(revoked_by_id, organization_id) REFERENCES users (id, organization_id) ON DELETE RESTRICT,
	FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE RESTRICT,
	CONSTRAINT fk_certificates_activity FOREIGN KEY(activity_id) REFERENCES activities (id) ON DELETE RESTRICT,
	CONSTRAINT fk_certificates_certificate_type FOREIGN KEY(certificate_type_id) REFERENCES certificate_types (id) ON DELETE RESTRICT
);

CREATE INDEX ix_certificates_batch_id ON certificates (batch_id);

CREATE INDEX ix_certificates_activity_id ON certificates (activity_id);

CREATE INDEX ix_certificates_verification_code ON certificates (verification_code);

CREATE INDEX ix_certificates_template_id ON certificates (template_id);

CREATE INDEX ix_certificates_certificate_type_id ON certificates (certificate_type_id);

CREATE INDEX ix_certificates_organization_id ON certificates (organization_id);

CREATE INDEX ix_certificates_created_by_id ON certificates (created_by_id);

CREATE TABLE batch_rows (
	id SERIAL NOT NULL,
	batch_id INTEGER NOT NULL,
	row_number INTEGER NOT NULL,
	recipient_reference VARCHAR(255),
	recipient_name VARCHAR(255),
	normalized_data JSONB NOT NULL,
	status VARCHAR(32) NOT NULL,
	error_code VARCHAR(100),
	error_message TEXT,
	certificate_id INTEGER,
	recipient_type certificate_recipient_type DEFAULT 'INTERNAL' NOT NULL,
	external_organization VARCHAR(255),
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT ck_batch_rows_row_number CHECK (row_number > 0),
	CONSTRAINT uq_batch_rows_batch_row_number UNIQUE (batch_id, row_number),
	CONSTRAINT fk_batch_rows_certificate_batch FOREIGN KEY(certificate_id, batch_id) REFERENCES certificates (id, batch_id) ON DELETE RESTRICT,
	FOREIGN KEY(batch_id) REFERENCES batches (id) ON DELETE CASCADE,
	UNIQUE (certificate_id)
);

CREATE INDEX ix_batch_rows_batch_id ON batch_rows (batch_id);

CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num));

INSERT INTO alembic_version (version_num) VALUES ('c8d9e0f1a2b3');

COMMIT;
