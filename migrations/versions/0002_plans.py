"""Add plan instances and their explicit location coverage."""

from alembic import op
import sqlalchemy as sa


revision = "0002_plans"
down_revision = "0001_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "plan_instances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("service_category", sa.String(32), nullable=False),
        sa.Column("plan_kind", sa.String(20), nullable=False),
        sa.Column("requirements", sa.Text(), nullable=False),
        sa.Column("owner_name", sa.String(120), nullable=False),
        sa.Column("planned_start_at", sa.DateTime(), nullable=False),
        sa.Column("planned_end_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_plan_version"),
        sa.CheckConstraint(
            "length(title) BETWEEN 1 AND 120", name="ck_plan_title"
        ),
        sa.CheckConstraint(
            "length(requirements) BETWEEN 1 AND 2000",
            name="ck_plan_requirements",
        ),
        sa.CheckConstraint(
            "length(owner_name) BETWEEN 1 AND 120", name="ck_plan_owner"
        ),
        sa.CheckConstraint(
            "service_category IN ('cleaning', 'water_supply', 'other')",
            name="ck_plan_category",
        ),
        sa.CheckConstraint(
            "plan_kind IN ('routine', 'special')", name="ck_plan_kind"
        ),
        sa.CheckConstraint(
            "planned_end_at > planned_start_at", name="ck_plan_period"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    op.create_table(
        "plan_locations",
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plan_instances.id"]),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("plan_id", "location_id"),
    )


def downgrade():
    op.drop_table("plan_locations")
    op.drop_table("plan_instances")