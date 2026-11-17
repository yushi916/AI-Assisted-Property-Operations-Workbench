"""Add execution claims and their explicitly reported location IDs."""

from alembic import op
import sqlalchemy as sa


revision = "0004_execution_records"
down_revision = "0003_location_hierarchy"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "execution_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("executor_name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("completion_claim", sa.String(32), nullable=False),
        sa.Column("claimed_started_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_finished_at", sa.DateTime(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_execution_version"),
        sa.CheckConstraint(
            "length(executor_name) BETWEEN 1 AND 120", name="ck_execution_executor"
        ),
        sa.CheckConstraint(
            "length(description) BETWEEN 1 AND 2000", name="ck_execution_description"
        ),
        sa.CheckConstraint(
            "completion_claim IN ('completed', 'partially_completed', 'not_completed', 'unknown')",
            name="ck_execution_claim",
        ),
        sa.CheckConstraint(
            "completion_claim = 'completed' OR claimed_finished_at IS NULL",
            name="ck_execution_finish_claim",
        ),
        sa.CheckConstraint(
            "claimed_started_at IS NULL OR claimed_finished_at IS NULL "
            "OR claimed_finished_at >= claimed_started_at",
            name="ck_execution_time_order",
        ),
        sa.CheckConstraint(
            "claimed_started_at IS NULL OR claimed_started_at <= recorded_at",
            name="ck_execution_start_recorded",
        ),
        sa.CheckConstraint(
            "claimed_finished_at IS NULL OR claimed_finished_at <= recorded_at",
            name="ck_execution_finish_recorded",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["plan_instances.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_index("ix_execution_records_plan_id", "execution_records", ["plan_id"])
    op.create_table(
        "execution_locations",
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["execution_records.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("execution_id", "location_id"),
    )


def downgrade():
    connection = op.get_bind()
    for table in ("execution_records", "execution_locations"):
        if connection.exec_driver_sql(f'SELECT 1 FROM "{table}" LIMIT 1').first():
            raise ValueError("已有执行证据，拒绝自动降级以免丢失记录")
    op.drop_table("execution_locations")
    op.drop_index("ix_execution_records_plan_id", table_name="execution_records")
    op.drop_table("execution_records")