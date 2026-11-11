"""Keep evidence agent suggestions and failures for human review."""

from alembic import op
import sqlalchemy as sa


revision = "0007_agent_runs"
down_revision = "0006_request_plan_links"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("evidence", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("suggestion", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("failure_stage", sa.String(24), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["report_id"], ["service_requests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND suggestion IS NULL AND evidence IS NULL "
            "AND failure_stage IS NULL AND error_code IS NULL) OR "
            "(status = 'pending_review' AND finished_at IS NOT NULL AND suggestion IS NOT NULL "
            "AND evidence IS NOT NULL AND failure_stage IS NULL AND error_code IS NULL) OR "
            "(status = 'failed' AND finished_at IS NOT NULL AND suggestion IS NULL "
            "AND failure_stage IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_agent_run_state",
        ),
    )
    op.create_index("ix_agent_runs_report_created", "agent_runs", ["report_id", "created_at", "id"])


def downgrade():
    if op.get_bind().exec_driver_sql("SELECT 1 FROM agent_runs LIMIT 1").first():
        raise ValueError("已有 Agent 运行记录，拒绝自动降级以免丢失记录")
    op.drop_index("ix_agent_runs_report_created", table_name="agent_runs")
    op.drop_table("agent_runs")