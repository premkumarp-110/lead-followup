"""Harvest real Lead Call API callIds that have a recording.

The seeded dataset is mock, but audio is proxied from the live CRM -- and a
callId we invent does not exist there. So seeded calls that carry
`has_recording: True` borrow a real callId (stored separately as `crm_call_id`)
purely so `GET /recording/{callId}` resolves. Nothing else about a seeded call
comes from the CRM.

Run this only when the fixture needs refreshing:

    python tools/harvest_recording_ids.py

Requires LEAD_CALL_API_KEY in backend/.env. Seeding works without the fixture --
`has_recording` calls simply end up with no `crm_call_id` and audio degrades to
a "not available" state.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.lead_call_client import LeadCallAPIError, get_lead_call_client  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "crm_recording_ids.json"
WANTED = 50
LEAD_PAGE = 100


def harvest() -> list[str]:
    client = get_lead_call_client()
    leads = client.search_leads(filters={"connectedMin": 1}, limit=LEAD_PAGE, batch=0)
    found: list[str] = []
    seen: set[str] = set()

    for lead in leads.get("rows", []):
        if len(found) >= WANTED:
            break
        try:
            calls = client.lead_calls(lead["leadId"], filters={"hasRecording": True}, limit=100)
        except LeadCallAPIError as exc:
            print(f"  skip {lead['leadId']}: {exc}")
            continue
        for call in calls.get("calls", []):
            call_id = call.get("callId")
            if call.get("hasRecording") and call_id and call_id not in seen:
                seen.add(call_id)
                found.append(call_id)
        time.sleep(0.18)  # stay well inside the 6 rps limit

    return found


def main() -> int:
    error = settings.lead_call_config_error()
    if error:
        print(f"Cannot harvest: {error}")
        return 1

    print(f"Harvesting up to {WANTED} callIds with recordings from {settings.lead_call_api_url} ...")
    try:
        call_ids = harvest()
    except LeadCallAPIError as exc:
        print(f"Harvest failed: {exc}")
        return 1

    if not call_ids:
        print("No recordings found; leaving the existing fixture untouched.")
        return 1

    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(
            {
                "_comment": (
                    "Real Lead Call API callIds that have a recording. Used ONLY to resolve "
                    "audio for seeded calls; all other seeded call data is mock. Regenerate "
                    "with backend/tools/harvest_recording_ids.py"
                ),
                "source": settings.lead_call_api_url,
                "harvested_count": len(call_ids),
                "call_ids": call_ids,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote {len(call_ids)} callIds to {FIXTURE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
