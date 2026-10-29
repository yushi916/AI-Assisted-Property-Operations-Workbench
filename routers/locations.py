from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from models import Location
from schemas import LocationRead


router = APIRouter(prefix="/api/locations", tags=["locations"])


@router.get("", response_model=list[LocationRead])
def list_locations(db: Session = Depends(get_db)):
    statement = select(Location).order_by(Location.id)
    locations = []

    for location in db.scalars(statement):
        locations.append(LocationRead.model_validate(location))

    return locations