"""Caller / BD endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database

from app.database import CALLERS, get_db
from app.models.caller import Caller

router = APIRouter(prefix="/api/callers", tags=["callers"])


@router.get("", response_model=list[Caller])
@router.get("/", response_model=list[Caller], include_in_schema=False)
def list_callers(include_inactive: bool = False, db: Database = Depends(get_db)) -> list[dict]:
    """Populates the Caller / BD dropdown in the call analyzer."""
    query = {} if include_inactive else {"active": {"$ne": False}}
    docs = db[CALLERS].find(query, {"_id": 0}).sort("name", 1)
    return list(docs)


@router.get("/{caller_id}", response_model=Caller)
def get_caller(caller_id: str, db: Database = Depends(get_db)) -> dict:
    caller = db[CALLERS].find_one({"caller_id": caller_id}, {"_id": 0})
    if caller is None:
        raise HTTPException(status_code=404, detail=f"Caller '{caller_id}' not found")
    return caller
