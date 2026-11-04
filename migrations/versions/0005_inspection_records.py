"""Add one-point inspection observations independently of execution claims."""

from alembic import op
import sqlalchemy as sa


revision = "0005_inspection_records"
down_revision = "0004_execution_records"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "inspection_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("inspector_name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("finding", sa.String(32), nullable=False),
        sa.Column("location_id", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_inspection_version"),
        sa.CheckConstraint(
            "length(inspector_name) BETWEEN 1 AND 120",
            name="ck_inspection_inspector",
        ),
        sa.CheckConstraint(
            "length(description) BETWEEN 1 AND 2000",
            name="ck_inspection_description",
        ),
        sa.CheckConstraint(
            "finding IN ('no_issue_observed', 'issue_observed', 'unable_to_verify')",
            name="ck_inspection_finding",
        ),
        sa.CheckConstraint(
            "observed_at IS NULL OR observed_at <= recorded_at",
            name="ck_inspection_observed_recorded",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plan_instances.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index(
        "ix_inspection_records_plan_id", "inspection_records", ["plan_id"]
    )


def downgrade():
    connection = op.get_bind()
    if connection.exec_driver_sql(
        "SELECT 1 FROM inspection_records LIMIT 1"
    ).first():
        raise ValueError("已有巡查证据，拒绝自动降级以免丢失记录")
    op.drop_index("ix_inspection_records_plan_id", table_name="inspection_records")
    op.drop_table("inspection_records")