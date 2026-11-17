"""Add compatible location kind and parent columns without rebuilding the table."""

from alembic import op


revision = "0003_location_hierarchy"
down_revision = "0002_plans"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE locations ADD COLUMN parent_id VARCHAR(64)
        CONSTRAINT fk_location_parent REFERENCES locations(id)
        CONSTRAINT ck_location_not_self
        CHECK (parent_id IS NULL OR parent_id <> id)
    """)
    op.execute("""
        ALTER TABLE locations ADD COLUMN kind VARCHAR(20)
        NOT NULL DEFAULT 'unknown'
        CONSTRAINT ck_location_kind
        CHECK (kind IN (
            'community', 'area', 'building', 'unit',
            'room', 'public_space', 'unknown'
        ))
        CONSTRAINT ck_location_parent_presence CHECK (
            (kind IN ('community', 'unknown') AND parent_id IS NULL)
            OR
            (kind IN ('area', 'building', 'unit', 'room', 'public_space')
             AND parent_id IS NOT NULL)
        )
    """)


def downgrade():
    connection = op.get_bind()
    assigned = connection.exec_driver_sql("""
        SELECT 1 FROM locations
        WHERE kind <> 'unknown' OR parent_id IS NOT NULL
        LIMIT 1
    """).first()
    if assigned is not None:
        raise ValueError("已有地点分类或父子关系，拒绝自动降级以免丢失信息")
    op.execute("ALTER TABLE locations DROP COLUMN kind")
    op.execute("ALTER TABLE locations DROP COLUMN parent_id")