"""Allow floor locations while preserving existing location IDs and links."""

from alembic import op


revision = "0008_floor_locations"
down_revision = "0007_agent_runs"
branch_labels = None
depends_on = None


def replace_location_checks(include_floor):
    connection = op.get_bind()

    if connection.dialect.name == "sqlite":
        if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
            raise RuntimeError(
                "楼层迁移须在事务开始前配置外键，请使用普通 Alembic 命令"
            )
        if connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='trigger' AND tbl_name='locations'"
        ).first():
            raise ValueError("地点表存在额外触发器，请先核对")

    child_kinds = "'area', 'building', 'unit', 'room', 'public_space'"
    if include_floor:
        child_kinds += ", 'floor'"

    with op.batch_alter_table("locations", recreate="always") as batch:
        batch.drop_constraint("ck_location_kind", type_="check")
        batch.drop_constraint("ck_location_parent_presence", type_="check")

        batch.create_check_constraint(
            "ck_location_kind",
            f"kind IN ('community', 'unknown', {child_kinds})",
        )
        batch.create_check_constraint(
            "ck_location_parent_presence",
            "(kind IN ('community', 'unknown') AND parent_id IS NULL) OR "
            f"(kind IN ({child_kinds}) AND parent_id IS NOT NULL)",
        )


def upgrade():
    replace_location_checks(include_floor=True)


def downgrade():
    if op.get_bind().exec_driver_sql(
        "SELECT 1 FROM locations WHERE kind='floor' LIMIT 1"
    ).first():
        raise ValueError("已有楼层地点，拒绝自动降级以免丢失台账")

    replace_location_checks(include_floor=False)