from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import get_db
from location_hierarchy import build_location_paths
from models import Location
from schemas import LocationRead


router = APIRouter(prefix="/api/locations", tags=["locations"])


@router.get(
    "",
    response_model=list[LocationRead],
    responses={503: {"description": "地点台账关系需核对，暂时无法提供地点列表"}},
)
def list_locations(db: Session = Depends(get_db)):
    statement = select(Location).order_by(Location.id)
    locations = list(db.scalars(statement))

    try:
        paths = build_location_paths(locations)
    except ValueError as error:
        raise HTTPException(
            status_code=503,
            detail=f"地点台账关系需核对：{error}",
        ) from error

    return [
        LocationRead(
            id=location.id,
            name=location.name,
            version=location.version,
            kind=location.kind,
            parent_id=location.parent_id,
            display_path=paths[location.id],
        )
        for location in locations
    ]