"""Add manual report-plan links and retain revocation history."""

from alembic import op
import sqlalchemy as sa


revision = "0006_request_plan_links"
down_revision = "0005_inspection_records"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "request_plan_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("linked_by", sa.String(120), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("linked_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("revocation_request_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", sa.String(120), nullable=True),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_link_version"),
        sa.CheckConstraint("length(trim(linked_by)) >= 1 AND length(linked_by) <= 120", name="ck_link_actor"),
        sa.CheckConstraint("length(trim(reason)) >= 1 AND length(reason) <= 2000", name="ck_link_reason"),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_link_status"),
        sa.CheckConstraint(
            "(status = 'active' AND revocation_request_id IS NULL AND revoked_at IS NULL "
            "AND revoked_by IS NULL AND revoke_reason IS NULL) OR "
            "(status = 'revoked' AND revocation_request_id IS NOT NULL AND revoked_at IS NOT NULL "
            "AND revoked_by IS NOT NULL AND revoke_reason IS NOT NULL AND version >= 2)",
            name="ck_link_revocation_fields",
        ),
        sa.CheckConstraint("revoked_at IS NULL OR revoked_at >= linked_at", name="ck_link_time_order"),
        sa.CheckConstraint(
            "revoked_by IS NULL OR (length(trim(revoked_by)) >= 1 AND length(revoked_by) <= 120)",
            name="ck_link_revoke_actor",
        ),
        sa.CheckConstraint(
            "revoke_reason IS NULL OR (length(trim(revoke_reason)) >= 1 AND length(revoke_reason) <= 2000)",
            name="ck_link_revoke_reason",
        ),
        sa.ForeignKeyConstraint(["report_id"], ["service_requests.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["plan_instances.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
        sa.UniqueConstraint("revocation_request_id"),
    )
    op.create_index("ix_request_plan_links_report_id", "request_plan_links", ["report_id"])
    op.create_index("ix_request_plan_links_plan_id", "request_plan_links", ["plan_id"])
    op.create_index(
        "uq_link_active_pair", "request_plan_links", ["report_id", "plan_id"],
        unique=True, sqlite_where=sa.text("status = 'active'"),
    )


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM request_plan_links LIMIT 1").first():
        raise ValueError("已有人工关联或撤销历史，拒绝自动降级以免丢失记录")
    op.drop_index("uq_link_active_pair", table_name="request_plan_links")
    op.drop_index("ix_request_plan_links_plan_id", table_name="request_plan_links")
    op.drop_index("ix_request_plan_links_report_id", table_name="request_plan_links")
    op.drop_table("request_plan_links")