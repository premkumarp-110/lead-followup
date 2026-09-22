"""Seed MongoDB with realistic sample data.

Run:  .venv/bin/python seed.py

Drops and recreates the four collections, then runs the *real* analysis flow
over the seeded calls -- so the dashboard you see is produced by
services/call_analyzer.py, not by hardcoded outcomes.

Two calls are deliberately left unprocessed so you can demo
POST /api/calls/{call_id}/process and watch a lead appear on the dashboard.

Follow-up times are generated relative to "now", so the Due Today / Overdue /
Upcoming buckets are always populated whenever you reseed.
"""

from datetime import datetime, timedelta, timezone

from app.database import (
    CALL_OUTCOMES,
    CALL_TRANSCRIPTS,
    CALLS,
    LEADS,
    close_client,
    ensure_indexes,
    get_db,
    ping,
)
from app.models.lead import LeadStatus
from app.services import followup_service
from app.services.call_analyzer import get_analyzer

NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)

BDS = {
    "priya": {"id": "BD001", "name": "Priya"},
    "rahul": {"id": "BD002", "name": "Rahul"},
    "arun": {"id": "BD003", "name": "Arun"},
    "meera": {"id": "BD004", "name": "Meera"},
}

FULL_STACK = "Full Stack Development"
DATA_SCIENCE = "Data Science"
AI_ML = "AI/ML"
CLOUD = "Cloud Computing"
CYBER = "Cyber Security"
ANALYTICS = "Data Analytics"


def when(**kwargs) -> datetime:
    return NOW + timedelta(**kwargs)


def spoken(target: datetime) -> str:
    """Render a follow-up time the way a lead would say it on a call.

    Explicit date + time keeps the deterministic analyzer's simple extractor
    accurate without needing a real natural-language date parser.
    """
    return f"{target.day} {target:%b %Y} at {target:%I:%M %p}".replace(" 0", " ")


# --------------------------------------------------------------------------
# Lead definitions. `target` is the follow-up moment the transcript asks for.
# --------------------------------------------------------------------------

LEADS_SPEC = [
    # ---------------- OVERDUE follow-ups ----------------
    {
        "lead_id": "L001", "name": "Arun Kumar", "phone": "+919840112233",
        "email": "arun.kumar@example.com", "course": FULL_STACK, "bd": "priya",
        "status": LeadStatus.INTERESTED, "created": when(days=-6),
        "calls": [{
            "ended": when(days=-3, hours=-1), "duration": 504,
            "target": when(hours=-2),
            "text": ("I am interested in the Full Stack Development course. I need to discuss "
                     "the fees with my parents. Please call me on {when}."),
        }],
        "expect": "OVERDUE",
    },
    {
        "lead_id": "L002", "name": "Divya Subramanian", "phone": "+919840223344",
        "email": "divya.s@example.com", "course": DATA_SCIENCE, "bd": "rahul",
        "status": LeadStatus.INTERESTED, "created": when(days=-8),
        "calls": [{
            "ended": when(days=-4), "duration": 372,
            "target": when(days=-1, hours=-3),
            "text": ("The Data Science syllabus looks good. I am travelling this week. "
                     "Please call me on {when}."),
        }],
        "expect": "OVERDUE",
    },
    {
        "lead_id": "L003", "name": "Vignesh Prabhu", "phone": "+919840334455",
        "email": "vignesh.p@example.com", "course": CLOUD, "bd": "rahul",
        "status": LeadStatus.CONTACTED, "created": when(days=-10),
        "calls": [
            {"ended": when(days=-9), "duration": 128,
             "target": when(days=-6),
             "text": "I just saw your message about Cloud Computing. Please call me on {when}."},
            {"ended": when(days=-6), "duration": 641,
             "target": when(days=-3, hours=-5),
             "text": ("I want to compare the AWS and Azure tracks before deciding. "
                      "Get back to me on {when}.")},
        ],
        "expect": "OVERDUE",
    },
    # ---------------- DUE TODAY ----------------
    {
        "lead_id": "L004", "name": "Sandhya Rajan", "phone": "+919840445566",
        "email": "sandhya.rajan@example.com", "course": AI_ML, "bd": "priya",
        "status": LeadStatus.INTERESTED, "created": when(days=-4),
        "calls": [{
            "ended": when(hours=-5), "duration": 455,
            "target": when(minutes=45),
            "text": ("The AI/ML placement support sounds useful. I am in a meeting right now, "
                     "please call me on {when}."),
        }],
        "expect": "DUE",
    },
    {
        "lead_id": "L005", "name": "Mohammed Irfan", "phone": "+919840556677",
        "email": "m.irfan@example.com", "course": CYBER, "bd": "meera",
        "status": LeadStatus.INTERESTED, "created": when(days=-5),
        "calls": [{
            "ended": when(hours=-7), "duration": 289,
            "target": when(minutes=95),
            "text": ("I want to know about the Cyber Security lab access and EMI options. "
                     "Please call me on {when}."),
        }],
        "expect": "DUE",
    },
    # ---------------- UPCOMING ----------------
    {
        "lead_id": "L006", "name": "Keerthana Iyer", "phone": "+919840667788",
        "email": "keerthana.iyer@example.com", "course": ANALYTICS, "bd": "arun",
        "status": LeadStatus.INTERESTED, "created": when(days=-3),
        "calls": [{
            "ended": when(hours=-20), "duration": 612,
            "target": when(days=1, hours=2),
            "text": ("Data Analytics looks like the right fit. I will discuss and let you know. "
                     "Please call me on {when}."),
        }],
        "expect": "UPCOMING",
    },
    {
        "lead_id": "L007", "name": "Naveen Balakrishnan", "phone": "+919840778899",
        "email": "naveen.b@example.com", "course": FULL_STACK, "bd": "rahul",
        "status": LeadStatus.INTERESTED, "created": when(days=-2),
        "calls": [{
            "ended": when(hours=-30), "duration": 337,
            "target": when(days=2, hours=1),
            "text": ("I am comparing two institutes for Full Stack Development. "
                     "Please call me on {when} with the final fee structure."),
        }],
        "expect": "UPCOMING",
    },
    {
        "lead_id": "L008", "name": "Priyanka Menon", "phone": "+919840889900",
        "email": "priyanka.menon@example.com", "course": DATA_SCIENCE, "bd": "meera",
        "status": LeadStatus.CONTACTED, "created": when(days=-12),
        "calls": [{
            "ended": when(days=-1, hours=-2), "duration": 218,
            "target": when(days=6, hours=3),
            "text": ("My notice period ends this month, so I am currently busy. "
                     "Please call me on {when}."),
        }],
        "expect": "UPCOMING",
    },
    # ---------------- CONVERTED ----------------
    {
        "lead_id": "L009", "name": "Karthik Raghavan", "phone": "+919840990011",
        "email": "karthik.r@example.com", "course": AI_ML, "bd": "priya",
        "status": LeadStatus.INTERESTED, "created": when(days=-14),
        "calls": [{
            "ended": when(hours=-26), "duration": 731,
            "target": None,
            "text": "I have completed the payment and I want to proceed with the course.",
        }],
        "expect": "CONVERTED",
    },
    {
        "lead_id": "L010", "name": "Aishwarya Nair", "phone": "+919841001122",
        "email": "aishwarya.nair@example.com", "course": DATA_SCIENCE, "bd": "arun",
        "status": LeadStatus.INTERESTED, "created": when(days=-11),
        "calls": [{
            "ended": when(days=-2), "duration": 566,
            "target": None,
            "text": ("I have decided to join the Data Science program. "
                     "Please send me the enrollment details."),
        }],
        "expect": "CONVERTED",
    },
    {
        "lead_id": "L011", "name": "Ramesh Venkatesan", "phone": "+919841112233",
        "email": "ramesh.v@example.com", "course": CLOUD, "bd": "meera",
        "status": LeadStatus.INTERESTED, "created": when(days=-9),
        "calls": [
            {"ended": when(days=-5), "duration": 402,
             "target": when(days=-3),
             "text": "The Cloud Computing course fits my plan. Please call me on {when}."},
            {"ended": when(days=-3), "duration": 288,
             "target": None,
             "text": "Payment completed through the link you sent. I want to enroll in the weekday batch."},
        ],
        "expect": "CONVERTED",
    },
    # ---------------- DROPPED ----------------
    {
        "lead_id": "L012", "name": "Sneha Pillai", "phone": "+919841223344",
        "email": "sneha.pillai@example.com", "course": FULL_STACK, "bd": "arun",
        "status": LeadStatus.CONTACTED, "created": when(days=-13),
        "calls": [{
            "ended": when(days=-2, hours=-4), "duration": 143,
            "target": None,
            "text": "I am not interested in the course anymore. Please don't contact me again.",
        }],
        "expect": "DROPPED",
    },
    {
        "lead_id": "L013", "name": "Gokul Srinivasan", "phone": "+919841334455",
        "email": "gokul.s@example.com", "course": CYBER, "bd": "rahul",
        "status": LeadStatus.CONTACTED, "created": when(days=-7),
        "calls": [{
            "ended": when(days=-3, hours=-6), "duration": 96,
            "target": None,
            "text": ("I have already joined another institute, so I am no longer interested. "
                     "Please cancel my application."),
        }],
        "expect": "DROPPED",
    },
    # ---------------- NEW: calls left UNPROCESSED for the live demo ----------------
    {
        "lead_id": "L014", "name": "Lakshmi Narayanan", "phone": "+919841445566",
        "email": "lakshmi.n@example.com", "course": ANALYTICS, "bd": "priya",
        "status": LeadStatus.NEW, "created": when(hours=-4),
        "calls": [{
            "ended": when(hours=-2), "duration": 384,
            "target": when(days=1, hours=4),
            "text": ("I saw the Data Analytics brochure and want to know about weekend batches. "
                     "Please call me on {when}."),
        }],
        "process": False,
        "expect": "UNPROCESSED -> FOLLOW_UP_REQUIRED",
    },
    {
        "lead_id": "L015", "name": "Harish Chandran", "phone": "+919841556677",
        "email": "harish.c@example.com", "course": AI_ML, "bd": "meera",
        "status": LeadStatus.NEW, "created": when(hours=-3),
        "calls": [{
            "ended": when(hours=-1), "duration": 512,
            "target": None,
            "text": "I have registered for the AI/ML program and completed the payment today.",
        }],
        "process": False,
        "expect": "UNPROCESSED -> CONVERTED",
    },
]


def build_documents():
    leads, calls, transcripts = [], [], []
    call_counter = 0

    for index, spec in enumerate(LEADS_SPEC, start=1):
        leads.append(
            {
                "lead_id": spec["lead_id"],
                "name": spec["name"],
                "phone": spec["phone"],
                "email": spec["email"],
                "course": spec["course"],
                "assigned_bd": BDS[spec["bd"]],
                "lead_status": spec["status"].value,
                "follow_up": dict(followup_service.NO_FOLLOW_UP),
                "follow_up_history": [],
                "last_call_at": None,
                "latest_call_id": None,
                "latest_outcome": None,
                "created_at": spec["created"],
                "updated_at": spec["created"],
            }
        )

        for call_spec in spec["calls"]:
            call_counter += 1
            call_id = f"CALL{call_counter:03d}"
            transcript_id = f"TRANSCRIPT{call_counter:03d}"
            ended = call_spec["ended"]
            started = ended - timedelta(seconds=call_spec["duration"])

            text = call_spec["text"]
            if call_spec.get("target"):
                text = text.format(when=spoken(call_spec["target"]))

            calls.append(
                {
                    "call_id": call_id,
                    "lead_id": spec["lead_id"],
                    "started_at": started,
                    "ended_at": ended,
                    "duration_seconds": call_spec["duration"],
                    "transcript_id": transcript_id,
                    "status": "COMPLETED",
                }
            )
            transcripts.append(
                {
                    "transcript_id": transcript_id,
                    "call_id": call_id,
                    "lead_id": spec["lead_id"],
                    "transcript": text,
                    "created_at": ended + timedelta(seconds=30),
                }
            )

    return leads, calls, transcripts


def main() -> None:
    ping()
    db = get_db()

    for collection in (LEADS, CALLS, CALL_TRANSCRIPTS, CALL_OUTCOMES):
        db[collection].drop()
    ensure_indexes()

    leads, calls, transcripts = build_documents()
    db[LEADS].insert_many(leads)
    db[CALLS].insert_many(calls)
    db[CALL_TRANSCRIPTS].insert_many(transcripts)

    print(f"Inserted {len(leads)} leads, {len(calls)} calls, {len(transcripts)} transcripts.\n")

    analyzer = get_analyzer()
    print(f"Running the analysis flow with analyzer '{analyzer.name}' (no LLM calls)...\n")
    print(f"{'LEAD':<6} {'NAME':<22} {'OUTCOME':<19} {'FOLLOW-UP':<18} BUCKET")
    print("-" * 82)

    skipped = []
    for spec in LEADS_SPEC:
        lead_calls = list(
            db[CALLS].find({"lead_id": spec["lead_id"]}).sort("ended_at", 1)
        )
        if spec.get("process") is False:
            for call in lead_calls:
                skipped.append((call["call_id"], spec["lead_id"], spec["name"]))
            print(f"{spec['lead_id']:<6} {spec['name']:<22} {'(left unprocessed)':<19} "
                  f"{'-':<18} -")
            continue

        for call in lead_calls:
            transcript = db[CALL_TRANSCRIPTS].find_one({"call_id": call["call_id"]})
            analysis = analyzer.analyze_call(
                transcript["transcript"],
                context={"call_ended_at": call["ended_at"], "lead_id": spec["lead_id"]},
            )
            followup_service.apply_analysis(db, call, analysis)

        lead = db[LEADS].find_one({"lead_id": spec["lead_id"]})
        follow_up = lead.get("follow_up") or {}
        bucket = followup_service.compute_bucket(follow_up, NOW).value
        shown = (
            f"{follow_up.get('date')} {follow_up.get('time')}"
            if follow_up.get("required")
            else "-"
        )
        flag = "" if bucket.startswith(spec["expect"][:4]) or spec["expect"] in (
            lead["latest_outcome"] or "",
        ) else "  <-- unexpected"
        print(f"{lead['lead_id']:<6} {lead['name']:<22} {lead['latest_outcome']:<19} "
              f"{shown:<18} {bucket}{flag}")

    print("\nLeft unprocessed on purpose (demo POST /api/calls/{call_id}/process):")
    for call_id, lead_id, name in skipped:
        print(f"  curl -X POST http://localhost:8000/api/calls/{call_id}/process   "
              f"# {lead_id} {name}")

    print(
        f"\nCollections: leads={db[LEADS].count_documents({})} "
        f"calls={db[CALLS].count_documents({})} "
        f"call_transcripts={db[CALL_TRANSCRIPTS].count_documents({})} "
        f"call_outcomes={db[CALL_OUTCOMES].count_documents({})}"
    )
    close_client()


if __name__ == "__main__":
    main()
