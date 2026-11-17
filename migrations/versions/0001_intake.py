"""Create the original report-intake schema."""

from alembic import op
import sqlalchemy as sa


revision = "0001_intake"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "locations",
        sa.Column("id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_location_version"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "service_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("service_category", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("risk_status", sa.String(40), nullable=False),
        sa.Column("location_id", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_request_version"),
        sa.CheckConstraint(
            "length(description) BETWEEN 1 AND 2000",
            name="ck_request_description",
        ),
        sa.CheckConstraint(
            "service_category IN ('cleaning', 'water_supply', 'other')",
            name="ck_request_category",
        ),
        sa.CheckConstraint(
            "source IN ('web', 'phone', 'on_site')",
            name="ck_request_source",
        ),
        sa.CheckConstraint(
            "risk_status IN ('no_immediate_risk_reported', 'urgent', 'unknown')",
            name="ck_request_risk",
        ),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )


def downgrade():
    op.drop_table("service_requests")
    op.drop_table("locations")