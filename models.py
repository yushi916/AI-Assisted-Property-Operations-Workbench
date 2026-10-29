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
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    version: Mapped[int] = mapped_column(Integer, default=1)


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