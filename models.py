from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from database import Base


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须带时区")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def utc_now():
    return datetime.now(UTC)


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_location_version"),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_location_not_self",
        ),
        CheckConstraint(
            """kind IN (
                'community', 'area', 'building', 'unit',
                'room', 'public_space', 'unknown'
            )""",
            name="ck_location_kind",
        ),
        CheckConstraint(
            """(kind IN ('community', 'unknown') AND parent_id IS NULL)
            OR
            (kind IN ('area', 'building', 'unit', 'room', 'public_space')
             AND parent_id IS NOT NULL)""",
            name="ck_location_parent_presence",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)

    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("locations.id", name="fk_location_parent")
    )
    kind: Mapped[str] = mapped_column(
        String(20), default="unknown", server_default="unknown"
    )


class ServiceRequest(Base):
    __tablename__ = "service_requests"
    __table_args__ = (
        CheckConstraint(
            "version >= 1",
            name="ck_request_version",
        ),
        CheckConstraint(
            "length(description) BETWEEN 1 AND 2000",
            name="ck_request_description",
        ),
        CheckConstraint(
            "service_category IN ('cleaning', 'water_supply', 'other')",
            name="ck_request_category",
        ),
        CheckConstraint(
            "source IN ('web', 'phone', 'on_site')",
            name="ck_request_source",
        ),
        CheckConstraint(
            "risk_status IN ('no_immediate_risk_reported', 'urgent', 'unknown')",
            name="ck_request_risk",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    service_category: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))
    risk_status: Mapped[str] = mapped_column(String(40))
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id"))
    observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    version: Mapped[int] = mapped_column(Integer, default=1)

    location: Mapped[Location | None] = relationship()



class PlanInstance(Base):
    __tablename__ = "plan_instances"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_plan_version"),
        CheckConstraint(
            "length(title) BETWEEN 1 AND 120", name="ck_plan_title"
        ),
        CheckConstraint(
            "length(requirements) BETWEEN 1 AND 2000",
            name="ck_plan_requirements",
        ),
        CheckConstraint(
            "length(owner_name) BETWEEN 1 AND 120", name="ck_plan_owner"
        ),
        CheckConstraint(
            "service_category IN ('cleaning', 'water_supply', 'other')",
            name="ck_plan_category",
        ),
        CheckConstraint(
            "plan_kind IN ('routine', 'special')", name="ck_plan_kind"
        ),
        CheckConstraint(
            "planned_end_at > planned_start_at", name="ck_plan_period"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    title: Mapped[str] = mapped_column(String(120))
    service_category: Mapped[str] = mapped_column(String(32))
    plan_kind: Mapped[str] = mapped_column(String(20), default="routine")
    requirements: Mapped[str] = mapped_column(Text)
    owner_name: Mapped[str] = mapped_column(String(120))
    planned_start_at: Mapped[datetime] = mapped_column(UTCDateTime())
    planned_end_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now)
    version: Mapped[int] = mapped_column(Integer, default=1)

    locations: Mapped[list["PlanLocation"]] = relationship(back_populates="plan")



class PlanLocation(Base):
    __tablename__ = "plan_locations"

    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plan_instances.id"), primary_key=True
    )
    location_id: Mapped[str] = mapped_column(
        ForeignKey("locations.id"), primary_key=True
    )

    plan: Mapped[PlanInstance] = relationship(back_populates="locations")
    location: Mapped[Location] = relationship()


class ExecutionRecord(Base):
    __tablename__ = "execution_records"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_execution_version"),
        CheckConstraint(
            "length(executor_name) BETWEEN 1 AND 120",
            name="ck_execution_executor",
        ),
        CheckConstraint(
            "length(description) BETWEEN 1 AND 2000",
            name="ck_execution_description",
        ),
        CheckConstraint(
            "completion_claim IN ('completed', 'partially_completed', 'not_completed', 'unknown')",
            name="ck_execution_claim",
        ),
        CheckConstraint(
            "completion_claim = 'completed' OR claimed_finished_at IS NULL",
            name="ck_execution_finish_claim",
        ),
        CheckConstraint(
            "claimed_started_at IS NULL OR claimed_finished_at IS NULL "
            "OR claimed_finished_at >= claimed_started_at",
            name="ck_execution_time_order",
        ),
        CheckConstraint(
            "claimed_started_at IS NULL OR claimed_started_at <= recorded_at",
            name="ck_execution_start_recorded",
        ),
        CheckConstraint(
            "claimed_finished_at IS NULL OR claimed_finished_at <= recorded_at",
            name="ck_execution_finish_recorded",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plan_instances.id"), index=True
    )
    executor_name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    completion_claim: Mapped[str] = mapped_column(String(32))
    claimed_started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    claimed_finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    recorded_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utc_now
    )
    version: Mapped[int] = mapped_column(Integer, default=1)

    plan: Mapped[PlanInstance] = relationship()
    locations: Mapped[list["ExecutionLocation"]] = relationship(
        back_populates="execution"
    )


class ExecutionLocation(Base):
    __tablename__ = "execution_locations"

    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("execution_records.id"), primary_key=True
    )
    location_id: Mapped[str] = mapped_column(
        ForeignKey("locations.id"), primary_key=True
    )

    execution: Mapped[ExecutionRecord] = relationship(
        back_populates="locations"
    )
    location: Mapped[Location] = relationship()