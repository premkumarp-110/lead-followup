"""Caller / BD domain model.

Callers live in their own collection so the "Analyze New Call" form can offer a
real dropdown, and so a call records *who made it* independently of which BD the
lead happens to be assigned to.
"""

from datetime import datetime

from pydantic import BaseModel


class Caller(BaseModel):
    caller_id: str
    name: str
    role: str = "Business Development"
    email: str | None = None
    phone: str | None = None
    active: bool = True
    created_at: datetime | None = None
