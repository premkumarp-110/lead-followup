"""Caller / BDA domain model.

The CRM has no /users or /callers endpoint, so this directory is *synthesized*
from the `ownerId / ownerName / ownerEmail` triples that appear on leads and on
calls. Both sources matter: measured live, a lead owned by one BDA routinely has
its calls made by others, so building the directory from leads alone leaves
`caller_id` lookups 404ing.

Field names follow the LeadSquared user schema, since that is the CRM this data
ultimately comes from:
https://apidocs.leadsquared.com/get-details-of-a-lead-owner/

Only `caller_id`, `name` and `email` come from the CRM. `role`, `team`,
`manager_name`, `user_type` and `phone` have no upstream source at all --
`salesOwnerId` / `salesOwnerName` are null on every lead measured -- so they are
generated locally and should be read as local metadata, not CRM truth.
"""

from datetime import datetime

from pydantic import BaseModel


class Caller(BaseModel):
    # ---- From the CRM ------------------------------------------------------
    caller_id: str                     # = ownerId
    name: str                          # = ownerName
    email: str | None = None           # = ownerEmail

    # ---- Derived from `name` -----------------------------------------------
    first_name: str | None = None
    last_name: str | None = None

    # ---- Local metadata (no CRM source) ------------------------------------
    phone: str | None = None
    role: str = "Sales"                # LeadSquared Role
    user_type: str = "User"            # LeadSquared UserType
    team: str | None = None
    manager_name: str | None = None
    status_code: str = "Active"        # LeadSquared StatusCode
    active: bool = True

    created_at: datetime | None = None

    @staticmethod
    def split_name(full_name: str) -> tuple[str, str]:
        """Best-effort first/last split. CRM names are free text -- single-word
        names ('Saravana') and multi-word names ('Praveen Kumar Subramanian')
        both occur, so never assume exactly two parts."""
        parts = (full_name or "").strip().split()
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], " ".join(parts[1:])
