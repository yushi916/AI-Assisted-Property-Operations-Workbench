"""Build an independent ORM reference with the pre-floor location checks."""

from sqlalchemy import CheckConstraint, MetaData

from database import Base


def create_pre_floor_schema(engine, table_names=None):
    metadata = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)

    locations = metadata.tables["locations"]
    for constraint in list(locations.constraints):
        if constraint.name in {
            "ck_location_kind",
            "ck_location_parent_presence",
        }:
            locations.constraints.remove(constraint)

    locations.append_constraint(CheckConstraint(
        "kind IN ( 'community', 'area', 'building', 'unit', "
        "'room', 'public_space', 'unknown' )",
        name="ck_location_kind",
    ))
    locations.append_constraint(CheckConstraint(
        "(kind IN ('community', 'unknown') AND parent_id IS NULL) OR "
        "(kind IN ('area', 'building', 'unit', 'room', 'public_space') "
        "AND parent_id IS NOT NULL)",
        name="ck_location_parent_presence",
    ))

    tables = (
        None
        if table_names is None
        else [metadata.tables[name] for name in table_names]
    )
    metadata.create_all(engine, tables=tables)