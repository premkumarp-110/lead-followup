"""Seed MongoDB with mock data in the Lead Call API's shape.

Run:  .venv/bin/python seed.py

Drops and recreates every collection, then inserts 60 leads, 12 callers,
40 calls, their transcripts and pre-baked analyses. No API calls and no LLM
calls are made, so reseeding is instant, free and works offline.

**The records are mock; the *shape* and the *distributions* are real.** Every
field the CRM returns is stored, and stage/product/source/language mixes follow
frequencies measured against the live API. That matters because filtering is
driven from this data alone.

**Every seeded BDA shares one mailbox** (`SEED_CALLER_EMAIL`). The reminder digest emails
a BD at their own address, and the CRM's real owners are live `@hclguvi.com` mailboxes --
seeding those would point a working SMTP scheduler at actual colleagues. Names stay realistic;
the address does not. Because the address no longer identifies a BD, the UI filters on
`caller_id`; `?bd=<email>` now legitimately matches every seeded lead.

Three properties of real data are reproduced deliberately:

  * **No name, phone or email.** The CRM exposes none. A lead is its `lead_id`
    plus `external_id`, with product/stage/owner as context.
  * **`conversion_date`, `sales_qualified` and `sales_owner_*` are null on every
    lead**, because they are null on every real lead. Nothing is invented to
    fill a column that is empty upstream.
  * **Transcript implies recording.** There is no call with a transcript but no
    audio, and the not-connected / zero-duration calls have neither.

Follow-up datetimes are generated relative to "now", so the Overdue / Due /
Upcoming / Unscheduled buckets are always populated after a reseed.

Calls that carry a recording borrow a real CRM `callId` from
fixtures/crm_recording_ids.json into `crm_call_id`, purely so audio playback
resolves. Nothing else about a seeded call comes from the CRM, and a missing
fixture only costs playback.
"""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import seed_data as V
from app.database import (
    CALL_ANALYSES,
    CALL_TRANSCRIPTS,
    CALLERS,
    CALLS,
    FOLLOWUP_ALERTS,
    LEADS,
    close_client,
    ensure_indexes,
    get_db,
    ping,
)
from app.models.analysis import NO_DATE_REASON, CallAnalysisResult
from app.models.call import CallStatus, Outcome
from app.models.caller import Caller
from app.models.lead import LeadStatus
from app.services import followup_service
from app.services.stage_mapping import map_stage

NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)
IST = timezone(timedelta(hours=5, minutes=30))
SEED_MODEL = "seed"
CRM_SOURCE = "transcript_content"

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "crm_recording_ids.json"

# Deterministic: reseeding twice gives the same dataset, so a bug is reproducible.
rng = random.Random(20260923)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def when(**kwargs) -> datetime:
    return NOW + timedelta(**kwargs)


def weighted(choices: list[tuple]) -> object:
    """Pick from [(value, weight), ...] using the live frequency weights."""
    values = [c[0] for c in choices]
    weights = [c[1] for c in choices]
    return rng.choices(values, weights=weights, k=1)[0]


def superleap_id() -> str:
    """A Superleap-style id: WN5qOX_ plus 8 mixed-case alphanumerics."""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "WN5qOX_" + "".join(rng.choice(alphabet) for _ in range(8))


def guid() -> str:
    """A LeadSquared-style GUID."""
    hexc = "0123456789abcdef"
    def block(n: int) -> str:
        return "".join(rng.choice(hexc) for _ in range(n))
    return f"{block(8)}-{block(4)}-{block(4)}-{block(4)}-{block(12)}"


def load_recording_ids() -> list[str]:
    """Real CRM callIds with recordings. Absent fixture just costs playback."""
    try:
        data = json.loads(FIXTURE.read_text())
        return list(data.get("call_ids") or [])
    except (OSError, ValueError):
        print(f"  ! {FIXTURE.name} not found -- seeded calls will have no playable audio.")
        return []


# --------------------------------------------------------------------------
# Callers
# --------------------------------------------------------------------------

def build_callers() -> list[dict]:
    """The BDA directory, in the LeadSquared user shape.

    Only id/name/email have a CRM source; role, team, manager and phone are
    local metadata because `salesOwner*` is null on every real lead.
    """
    docs = []
    managers = ["Saravana", "Rizwan Quadir", "Vignesh VR"]
    for index, name in enumerate(V.CALLER_NAMES):
        first, last = Caller.split_name(name)
        docs.append({
            "caller_id": superleap_id(),
            "name": name,
            # Deliberately the same mailbox for every seeded BDA -- see
            # SEED_CALLER_EMAIL in seed_data.py. A reminder digest goes to the
            # BD's own address, and the CRM's real owners are live mailboxes.
            "email": V.SEED_CALLER_EMAIL,
            "first_name": first,
            "last_name": last,
            "phone": f"+9196000{11000 + index}",
            "role": V.ROLES[index % len(V.ROLES)],
            "user_type": "User",
            "team": V.TEAMS[index % len(V.TEAMS)],
            "manager_name": managers[index % len(managers)],
            "status_code": "Active",
            # Two inactive BDs: their leads must still be visible and the
            # dropdown must still list them, marked.
            "active": index not in (10, 11),
            "created_at": when(days=-(120 + index * 5)),
        })
    return docs


# --------------------------------------------------------------------------
# Leads
# --------------------------------------------------------------------------

# What each call-active lead is built to demonstrate. The stage, the transcript
# closer and the analysis outcome are all derived from this, so they agree.
#   (bucket, count)
ACTIVE_PLAN = [
    ("OVERDUE", 5),
    ("DUE", 3),
    ("UPCOMING", 4),
    ("UNSCHEDULED", 3),
    ("CONVERTED", 3),
    ("DROPPED", 3),
    ("NO_ANALYSIS", 7),   # connected with a recording but no transcript yet
    ("NOT_ANALYZABLE", 12),  # not connected / zero duration
]

# Follow-up closers per bucket, and the offset each implies.
BUCKET_CLOSERS = {
    "OVERDUE": ["tomorrow_11", "tomorrow_10", "evening_8", "tomorrow_12", "next_week"],
    "DUE": ["tomorrow_11", "evening_8", "tomorrow_12"],
    "UPCOMING": ["tomorrow_11", "tomorrow_12", "tomorrow_10", "next_week"],
    "UNSCHEDULED": ["no_date", "no_date_ta", "no_date"],
    "CONVERTED": ["converted"] * 3,
    "DROPPED": ["dropped", "dropped_ta", "dropped"],
}


def stage_for(bucket: str) -> str:
    if bucket == "CONVERTED":
        return rng.choice(V.STAGES_CONVERTED)
    if bucket == "DROPPED":
        return rng.choice(V.STAGES_DROPPED)
    if bucket in ("NOT_ANALYZABLE", "NO_ANALYSIS"):
        return rng.choice(V.STAGES_CONTACTED)
    return rng.choice(V.STAGES_FOLLOW_UP)


def follow_up_target(bucket: str) -> datetime | None:
    """The datetime our analysis extracts, positioned to land in `bucket`."""
    if bucket == "OVERDUE":
        return when(hours=-rng.choice([3, 8, 26, 50, 100]))
    if bucket == "DUE":
        return when(minutes=rng.choice([25, 55, 95]))
    if bucket == "UPCOMING":
        return when(days=rng.choice([1, 2, 3, 6]), hours=rng.choice([1, 3, 5]))
    return None


def build_lead(bucket: str | None, caller: dict, call_active: bool) -> dict:
    """One lead with every CRM field populated the way the CRM populates it."""
    stage = stage_for(bucket) if call_active else rng.choice(V.STAGES_NEW + V.STAGES_CONTACTED[:3])
    created = when(days=-rng.randint(20, 400), hours=-rng.randint(0, 23))

    lead = {
        "lead_id": superleap_id(),
        # ~4% of real leads have no externalId.
        "external_id": guid() if rng.random() > 0.04 else None,
        "stage": stage,
        "previous_stage": "New" if call_active and rng.random() < 0.78 else None,
        "product": weighted(V.PRODUCTS),
        "language": weighted(V.LANGUAGES) if call_active else None,
        "win_probability": None,
        "segmentation": weighted(V.SEGMENTATIONS),
        # Null on every real lead. Kept in the schema, never invented.
        "sales_qualified": None,
        "city": weighted(V.CITIES),
        "state": weighted(V.STATES),
        "country": "India" if rng.random() < 0.38 else None,
        "lead_source": weighted(V.LEAD_SOURCES),
        "source_campaign": weighted(V.SOURCE_CAMPAIGNS),
        "source_medium": weighted(V.SOURCE_MEDIUMS),
        "source_content": weighted(V.SOURCE_CONTENTS),
        "last_source": None,
        "last_medium": None,
        "nurturing": rng.choice(V.NURTURING_URLS) if rng.random() < 0.012 else None,
        "last_disposition_status": None,
        "last_sub_disposition_status": None,
        "last_call_attempted_at": None,
        "calls_connected": 0,
        "calls_missed": 0,
        "total_attempts": 0,
        "total_talktime_sec": 0,
        "first_contact_date": None,
        # 100% null upstream -- conversion is derived from stage, never this.
        "conversion_date": None,
        "created_at": created,
        "updated_at": created,
        "owner_id": caller["caller_id"],
        "owner_name": caller["name"],
        "owner_email": caller["email"],
        # 100% null upstream: there is no sales-owner data in the CRM at all.
        "sales_owner_id": None,
        "sales_owner_name": None,
        # Ours.
        "lead_status": map_stage(stage).value,
        "follow_up": dict(followup_service.NO_FOLLOW_UP),
        "follow_up_history": [],
        "last_call_at": None,
        "latest_call_id": None,
        "latest_outcome": None,
        "latest_sentiment": None,
        "latest_analysis_id": None,
    }

    if call_active:
        # Call-active leads carry the fields that are ~92% null globally but
        # populated on every lead that has actually been called.
        lead["first_contact_date"] = created + timedelta(days=rng.randint(1, 10))
        lead["win_probability"] = float(rng.choice([5, 10, 15, 25, 45, 55, 65, 70, 72]))
        lead["last_disposition_status"] = (
            "Connected" if bucket != "NOT_ANALYZABLE" else "Not Connected"
        )
        lead["last_sub_disposition_status"] = stage
    return lead


# --------------------------------------------------------------------------
# Transcripts
# --------------------------------------------------------------------------

def build_transcript_text(closer_key: str, tamil: bool) -> str:
    """A transcript in the CRM's real format: [MM:SS] Agent:/Customer: lines."""
    opener = rng.choice(V.OPENERS_TA if tamil else V.OPENERS_EN)
    middle = rng.choice(V.MIDDLES_TA if tamil else V.MIDDLES_EN)
    closer = V.CLOSERS[closer_key]

    lines: list[tuple[str, str]] = [*opener, *middle, *closer]
    out: list[str] = []
    seconds = 0
    for speaker, text in lines:
        out.append(f"[{seconds // 60:02d}:{seconds % 60:02d}] {speaker}: {text}")
        seconds += rng.randint(3, 14)
    return "\n".join(out)


# --------------------------------------------------------------------------
# analysisSummary (the CRM's own analysis)
# --------------------------------------------------------------------------

def build_analysis_summary(lead: dict, closer_key: str, pitch: int) -> tuple[str, int]:
    """The CRM's analysis JSON, with all ten autofill questions present.

    Returns (json_string, violation_count). The follow-up answer is prose and
    never carries a timestamp -- extracting one is our job, not the CRM's.
    """
    status = rng.choice(V.PROSPECT_STATUSES)
    product = lead.get("product") or "the programme"
    next_step = (
        "Customer agreed to review the details and confirm."
        if closer_key.startswith("no_date")
        else "A specific follow-up was agreed at the end of the call."
    )
    summary = rng.choice(V.SUMMARY_TEMPLATES).format(
        status=status.lower(), product=product, next_step=next_step
    )

    violation_count = rng.choice([0, 0, 0, 0, 1, 1, 2, 2, 3, 4])
    violations = [
        {"severity": sev, "issue": issue, "detail": detail}
        for sev, issue, detail in rng.sample(V.VIOLATIONS, violation_count)
    ] if violation_count else []

    answers = [
        lead.get("city") or "n/a",
        rng.choice(["yes", "no"]),
        rng.choice(V.PAYMENT_OPTIONS),
        rng.choice(["yes", "no"]),
        product,
        status,
        "yes" if closer_key == "converted" else "no",
        lead.get("language") or "n/a",
        rng.choice(V.QUALIFICATIONS),
        V.FOLLOW_UP_PROSE[closer_key],
    ]

    payload = {
        "call_summary": summary,
        "performance_metrics": {
            "pitch_score_percent": pitch,
            "win_probability": int(lead.get("win_probability") or 50),
        },
        "findings": {
            "autofill_data": [
                {"question": q, "answer": a}
                for q, a in zip(V.AUTOFILL_QUESTIONS, answers)
            ],
            "violations": violations,
            "improvement_tips": rng.sample(V.IMPROVEMENT_TIPS, rng.randint(2, 4)),
        },
    }
    return json.dumps(payload), violation_count


# --------------------------------------------------------------------------
# Analyses
# --------------------------------------------------------------------------

INTENT_BY_BUCKET = {
    "OVERDUE": "INTERESTED", "DUE": "INTERESTED", "UPCOMING": "NEEDS_TIME",
    "UNSCHEDULED": "NEEDS_TIME", "CONVERTED": "READY_TO_ENROLL", "DROPPED": "NOT_INTERESTED",
}
OUTCOME_BY_BUCKET = {
    "OVERDUE": Outcome.FOLLOW_UP_REQUIRED, "DUE": Outcome.FOLLOW_UP_REQUIRED,
    "UPCOMING": Outcome.FOLLOW_UP_REQUIRED, "UNSCHEDULED": Outcome.FOLLOW_UP_REQUIRED,
    "CONVERTED": Outcome.CONVERTED, "DROPPED": Outcome.DROPPED,
}


# Sentiment per scripted bucket, as (label, trajectory, score-range) weighted
# choices. Chosen so the seeded worklist exercises the ordering rule visibly:
# OVERDUE deliberately spans the whole range, including a DECLINED lead that
# must sort above older but happier ones.
SENTIMENT_BY_BUCKET = {
    "OVERDUE": [
        ("NEGATIVE", "DECLINED", (-0.8, -0.4)),
        ("NEUTRAL", "DECLINED", (-0.3, 0.0)),
        ("NEGATIVE", "STABLE", (-0.7, -0.3)),
        ("MIXED", "STABLE", (-0.2, 0.2)),
        ("POSITIVE", "IMPROVED", (0.3, 0.7)),
    ],
    "DUE": [
        ("MIXED", "STABLE", (-0.2, 0.3)),
        ("POSITIVE", "IMPROVED", (0.4, 0.8)),
        ("NEUTRAL", "STABLE", (-0.1, 0.2)),
    ],
    "UPCOMING": [
        ("POSITIVE", "IMPROVED", (0.4, 0.8)),
        ("NEUTRAL", "STABLE", (-0.1, 0.2)),
        # One un-judgeable call, so the "not assessed" path is rendered too.
        ("UNKNOWN", "UNKNOWN", None),
        ("MIXED", "DECLINED", (-0.3, 0.1)),
    ],
    "UNSCHEDULED": [
        ("NEUTRAL", "STABLE", (-0.2, 0.1)),
        ("MIXED", "STABLE", (-0.3, 0.2)),
        ("UNKNOWN", "UNKNOWN", None),
    ],
    "CONVERTED": [("POSITIVE", "IMPROVED", (0.6, 0.95))],
    "DROPPED": [("NEGATIVE", "DECLINED", (-0.95, -0.55))],
}

# Verbatim lines lifted from the transcript closers in seed_data, so the
# evidence quote is genuinely something the customer said on that call.
SENTIMENT_EVIDENCE = {
    "POSITIVE": ["Okay, set up a Google Meet tomorrow at 11 AM.",
                 "I have made the payment just now, please confirm."],
    "NEUTRAL": ["Send everything on WhatsApp, I will review and get back.",
                "WhatsApp-la anuppunga, naan paathutu sollren."],
    "MIXED": ["Okay, my English is not good, that's why I-",
              "fees evlo aagum-nu mattum sollunga."],
    "NEGATIVE": ["ippo vendaam, naan paarkala.",
                 "enakku interest illa, naan vera place-la join panniten.",
                 "I am not interested, I already joined somewhere else."],
    "UNKNOWN": [None],
}


# Cycled, not randomly picked. With only 3-5 leads per bucket, random choice
# regularly produces five negative OVERDUE leads and the sentiment ordering
# becomes invisible -- both to a reader and to any test asserting it. Cycling
# guarantees each bucket shows its full declared spread every reseed.
_sentiment_cursor: dict[str, int] = {}


def build_sentiment(bucket: str) -> dict:
    """A sentiment block consistent with the bucket's scripted outcome."""
    options = SENTIMENT_BY_BUCKET[bucket]
    index = _sentiment_cursor.get(bucket, 0)
    _sentiment_cursor[bucket] = index + 1
    label, trajectory, score_range = options[index % len(options)]
    return {
        "label": label,
        "score": round(rng.uniform(*score_range), 2) if score_range else None,
        "trajectory": trajectory,
        "evidence": rng.choice(SENTIMENT_EVIDENCE[label]),
    }


def build_analysis_result(bucket: str, target: datetime | None, lead: dict) -> CallAnalysisResult:
    """The validated analysis, as the pipeline would have produced it."""
    outcome = OUTCOME_BY_BUCKET[bucket]
    product = lead.get("product") or "the programme"

    if outcome is Outcome.CONVERTED:
        payload = {
            "outcome": outcome.value, "follow_up_required": False, "follow_up": None,
            "customer_intent": "READY_TO_ENROLL",
            "summary": f"The lead confirmed payment for {product} and is enrolled.",
            "key_points": ["Payment confirmed on the call", "Batch start communicated"],
            "confidence": round(rng.uniform(0.88, 0.97), 2),
            "sentiment": build_sentiment(bucket),
        }
    elif outcome is Outcome.DROPPED:
        payload = {
            "outcome": outcome.value, "follow_up_required": False, "follow_up": None,
            "customer_intent": "NOT_INTERESTED",
            "summary": f"The lead declined {product} and asked not to be contacted further.",
            "key_points": ["Explicitly not interested", "Chose another provider"],
            "confidence": round(rng.uniform(0.85, 0.96), 2),
            "sentiment": build_sentiment(bucket),
        }
    else:
        local = target.astimezone(IST) if target else None
        payload = {
            "outcome": outcome.value,
            "follow_up_required": True,
            "follow_up": {
                "date": local.strftime("%Y-%m-%d") if local else None,
                "time": local.strftime("%H:%M") if local else None,
                "datetime": local.isoformat() if local else None,
                "reason": (
                    f"The lead asked for a call back about {product}."
                    if local else NO_DATE_REASON
                ),
            },
            "customer_intent": INTENT_BY_BUCKET[bucket],
            "summary": (
                f"The lead is considering {product} and asked for details before deciding."
            ),
            "key_points": ["Details to be shared on WhatsApp", "Fees and EMI discussed"],
            "confidence": round(rng.uniform(0.72, 0.94), 2),
            "sentiment": build_sentiment(bucket),
        }
    return CallAnalysisResult.model_validate(payload)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    ping()
    db = get_db()

    # followup_alerts is dropped too: stale delivery rows would make the
    # once-a-day dedupe skip the first send after a reseed.
    for name in (LEADS, CALLERS, CALLS, CALL_TRANSCRIPTS, CALL_ANALYSES,
                 FOLLOWUP_ALERTS, "call_outcomes"):
        db.drop_collection(name)
    ensure_indexes()

    callers = build_callers()
    db[CALLERS].insert_many([dict(c) for c in callers])

    recording_ids = load_recording_ids()
    rec_index = 0

    leads_docs: list[dict] = []
    calls_docs: list[dict] = []
    transcripts_docs: list[dict] = []
    analyses_docs: list[dict] = []

    # ---- Call-active leads ------------------------------------------------
    for bucket, count in ACTIVE_PLAN:
        for _ in range(count):
            caller = rng.choice(callers)
            lead = build_lead(bucket, caller, call_active=True)

            analyzable = bucket not in ("NOT_ANALYZABLE",)
            has_transcript = bucket not in ("NOT_ANALYZABLE", "NO_ANALYSIS")
            has_recording = analyzable

            # The call itself.
            attempts = rng.randint(1, 4)
            duration = 0 if not analyzable else rng.choice([48, 77, 96, 145, 215, 402, 600])
            ended = when(days=-rng.randint(0, 6), hours=-rng.randint(1, 20))
            started = ended - timedelta(seconds=duration)
            # Call owner is often NOT the lead owner -- true in the real data,
            # and the reason the directory is built from both sources.
            call_owner = rng.choice(callers) if rng.random() < 0.35 else caller
            direction = "inbound" if rng.random() < 0.13 else "outbound"

            if analyzable:
                telephony, final = "connected", "completed"
            elif rng.random() < 0.1:
                telephony, final = "missed_call", "agent_unanswered"
            else:
                telephony, final = "not_connected", "customer_canceled"

            call_id = f"CALL-{superleap_id()[7:]}"
            crm_call_id = None
            if has_recording and rec_index < len(recording_ids):
                crm_call_id = recording_ids[rec_index]
                rec_index += 1

            closer_key = rng.choice(BUCKET_CLOSERS[bucket]) if bucket in BUCKET_CLOSERS else "no_date"
            tamil = (lead.get("language") in {"Tamil", "Malayalam"}) or closer_key.endswith("_ta")

            # The CRM analysed ~37% of calls; pitch/violations come with it.
            pitch = None
            analysis_summary = None
            violations = 0
            if analyzable and rng.random() < 0.55:
                pitch = rng.choice([15, 18, 19, 28, 29, 34, 43, 44, 49, 51, 62, 66, 67, 70])
                analysis_summary, violations = build_analysis_summary(lead, closer_key, pitch)

            call = {
                "call_id": call_id,
                "lead_id": lead["lead_id"],
                "caller_id": call_owner["caller_id"],
                "crm_call_id": crm_call_id,
                "call_time": started,
                "start_time": started,
                "end_time": ended,
                "direction": direction,
                "duration_sec": duration,
                "telephony_status": telephony,
                "final_status": final,
                "pitch_score": float(pitch) if pitch is not None else None,
                "violations": violations,
                "analysis_summary": analysis_summary,
                "analysis_summary_parsed": json.loads(analysis_summary) if analysis_summary else None,
                "has_recording": has_recording,
                "has_transcript": has_transcript,
                "recording_path": f"/recording/{crm_call_id}" if crm_call_id else None,
                "transcript_path": f"/transcript/{crm_call_id}" if (has_transcript and crm_call_id) else None,
                "owner_id": call_owner["caller_id"],
                "owner_name": call_owner["name"],
                "owner_email": call_owner["email"],
                "status": CallStatus.PENDING.value,
                "error": None,
                "failed_stage": None,
                "transcript_id": None,
                "analysis_id": None,
                "created_at": ended,
                "processed_at": None,
                "seeded": True,
            }

            # Lead call-activity counters, consistent with the call.
            lead["total_attempts"] = attempts
            lead["calls_connected"] = 1 if telephony == "connected" else 0
            lead["calls_missed"] = 1 if telephony != "connected" else 0
            lead["total_talktime_sec"] = duration
            lead["last_call_attempted_at"] = ended
            lead["last_call_at"] = ended
            lead["latest_call_id"] = call_id
            lead["updated_at"] = ended

            if not analyzable:
                call["status"] = CallStatus.NOT_ANALYZABLE.value
                call["error"] = "This call has neither a transcript nor a recording."

            # ---- Transcript ----
            if has_transcript:
                transcript = {
                    "transcript_id": f"TR-{superleap_id()[7:]}",
                    "call_id": call_id,
                    "lead_id": lead["lead_id"],
                    "caller_id": call_owner["caller_id"],
                    "transcript": build_transcript_text(closer_key, tamil),
                    "language": "ta-IN" if tamil else "en-IN",
                    "duration_seconds": duration,
                    "provider": SEED_MODEL,
                    "source": CRM_SOURCE,
                    "created_at": ended + timedelta(seconds=30),
                }
                transcripts_docs.append(transcript)
                call["transcript_id"] = transcript["transcript_id"]

            # ---- Analysis (only where there is a transcript to justify it) ----
            if has_transcript and bucket in OUTCOME_BY_BUCKET:
                target = follow_up_target(bucket)
                result = build_analysis_result(bucket, target, lead)
                analysis = {
                    "analysis_id": f"AN-{superleap_id()[7:]}",
                    "call_id": call_id,
                    "lead_id": lead["lead_id"],
                    "caller_id": call_owner["caller_id"],
                    "model": SEED_MODEL,
                    "status": "COMPLETED",
                    "outcome": result.outcome.value,
                    "follow_up_required": result.follow_up_required,
                    "follow_up": result.follow_up.model_dump() if result.follow_up else None,
                    "customer_intent": result.customer_intent.value,
                    "summary": result.summary,
                    "key_points": result.key_points,
                    "confidence": result.confidence,
                    "sentiment": result.sentiment.model_dump(mode="json"),
                    "raw_response": None,
                    "error": None,
                    "degraded": False,
                    "created_at": ended + timedelta(minutes=1),
                }
                analyses_docs.append(analysis)

                call["status"] = CallStatus.COMPLETED.value
                call["analysis_id"] = analysis["analysis_id"]
                call["processed_at"] = analysis["created_at"]

                # Apply to the lead, exactly as apply_analysis_to_lead would.
                lead["lead_status"] = followup_service.OUTCOME_TO_LEAD_STATUS[
                    result.outcome
                ].value
                lead["follow_up"] = followup_service.follow_up_block_from_analysis(result)
                lead["latest_outcome"] = result.outcome.value
                lead["latest_sentiment"] = result.sentiment.model_dump(mode="json")
                lead["latest_analysis_id"] = analysis["analysis_id"]

            calls_docs.append(call)
            leads_docs.append(lead)

    # ---- Never-called leads (95% of production; 24 here) ------------------
    for _ in range(60 - len(leads_docs)):
        leads_docs.append(build_lead(None, rng.choice(callers), call_active=False))

    db[LEADS].insert_many(leads_docs)
    db[CALLS].insert_many(calls_docs)
    if transcripts_docs:
        db[CALL_TRANSCRIPTS].insert_many(transcripts_docs)
    if analyses_docs:
        db[CALL_ANALYSES].insert_many(analyses_docs)

    _report(db, leads_docs, calls_docs, transcripts_docs, analyses_docs, callers, rec_index)


def _report(db, leads, calls, transcripts, analyses, callers, rec_used) -> None:
    now = followup_service.utcnow()
    buckets: dict[str, int] = {}
    for lead in leads:
        bucket = followup_service.compute_bucket(lead.get("follow_up"), now).value
        buckets[bucket] = buckets.get(bucket, 0) + 1

    print(f"\nSeeded {len(leads)} leads, {len(callers)} callers, {len(calls)} calls, "
          f"{len(transcripts)} transcripts, {len(analyses)} analyses.")
    print(f"  recordings linked to real CRM callIds: {rec_used}")

    call_active = [l for l in leads if l["total_attempts"] > 0]
    print(f"  call-active leads: {len(call_active)}  |  never called: {len(leads) - len(call_active)}")
    print("  follow-up buckets: " + ", ".join(f"{k}={v}" for k, v in sorted(buckets.items())))
    print("  call status:       " + ", ".join(
        f"{s}={sum(1 for c in calls if c['status'] == s)}"
        for s in sorted({c["status"] for c in calls})))
    print(f"  has_recording={sum(1 for c in calls if c['has_recording'])}  "
          f"has_transcript={sum(1 for c in calls if c['has_transcript'])}  "
          f"analysis_summary={sum(1 for c in calls if c['analysis_summary'])}  "
          f"inbound={sum(1 for c in calls if c['direction'] == 'inbound')}")

    # Invariants worth asserting rather than eyeballing.
    assert all("name" not in l and "phone" not in l and "email" not in l for l in leads), \
        "leads must carry no PII -- the CRM exposes none"
    assert all(l["conversion_date"] is None for l in leads), "conversion_date is null upstream"
    assert all(l["sales_qualified"] is None for l in leads), "sales_qualified is null upstream"
    assert all(l["sales_owner_id"] is None and l["sales_owner_name"] is None for l in leads), \
        "sales owner is null upstream"
    assert all(c["has_recording"] for c in calls if c["has_transcript"]), \
        "transcript implies recording"

    # The reminder digest emails a BD at their own address, and a real SMTP
    # account is configured. If a real mailbox ever gets seeded, the scheduler
    # mails an actual colleague. Fail the seed rather than let that ship.
    addresses = (
        {c.get("email") for c in callers}
        | {lead.get("owner_email") for lead in leads}
        | {call.get("owner_email") for call in calls}
    ) - {None}
    stray = sorted(a for a in addresses if a != V.SEED_CALLER_EMAIL)
    assert not stray, (
        f"Seeded a mailbox that is not {V.SEED_CALLER_EMAIL}: {stray}. "
        "Reminder digests would be delivered to it."
    )

    analysed = [l for l in leads if l.get("latest_analysis_id")]
    sentiments: dict[str, int] = {}
    for lead in analysed:
        label = (lead.get("latest_sentiment") or {}).get("label", "?")
        sentiments[label] = sentiments.get(label, 0) + 1
    print("  sentiment:         " + ", ".join(f"{k}={v}" for k, v in sorted(sentiments.items())))

    assert all(l.get("latest_sentiment") for l in analysed), \
        "every analysed lead must carry a denormalized sentiment"
    assert all(
        (l["latest_sentiment"].get("score") is None)
        for l in analysed
        if l["latest_sentiment"]["label"] == "UNKNOWN"
    ), "UNKNOWN sentiment must never carry a score"

    print("  invariants: no PII, conversion/sales-owner null, transcript implies recording,")
    print(f"              every address is {V.SEED_CALLER_EMAIL}  OK")


if __name__ == "__main__":
    try:
        main()
    finally:
        close_client()
