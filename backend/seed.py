"""Seed MongoDB with realistic sample data.

Run:  .venv/bin/python seed.py

Drops and recreates the five collections, then inserts 15 leads, 5 callers,
17 calls, 17 transcripts and 17 analyses. Analyses are PRE-BAKED documents
(model = "seed") -- no Vertex AI calls are made, so reseeding is instant, free
and works offline. Live Gemini analysis is exercised by uploading a real
recording through the dashboard.

Follow-up times are generated relative to "now", so the Due Today / Overdue /
Upcoming buckets are always populated whenever you reseed.

Seeded calls have transcripts but no audio file (they were never uploaded), so
the modal shows "recording not available" for them. That is expected.
"""

from datetime import datetime, timedelta, timezone

from app.database import (
    CALL_ANALYSES,
    CALL_TRANSCRIPTS,
    CALLERS,
    CALLS,
    LEADS,
    close_client,
    ensure_indexes,
    get_db,
    ping,
)
from app.models.analysis import NO_DATE_REASON, CallAnalysisResult
from app.models.call import CallStatus, Outcome
from app.models.lead import LeadStatus
from app.services import followup_service

NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)
IST = timezone(timedelta(hours=5, minutes=30))
SEED_MODEL = "seed"

# --------------------------------------------------------------------------
# Callers / BDs (5)
# --------------------------------------------------------------------------

CALLERS_SPEC = [
    {"caller_id": "BD001", "name": "Priya Raman", "role": "Senior BD Executive",
     "email": "priya.raman@edtech.example", "phone": "+919600011001"},
    {"caller_id": "BD002", "name": "Rahul Nair", "role": "BD Executive",
     "email": "rahul.nair@edtech.example", "phone": "+919600011002"},
    {"caller_id": "BD003", "name": "Arun Prasad", "role": "BD Executive",
     "email": "arun.prasad@edtech.example", "phone": "+919600011003"},
    {"caller_id": "BD004", "name": "Meera Krishnan", "role": "BD Executive",
     "email": "meera.k@edtech.example", "phone": "+919600011004"},
    {"caller_id": "BD005", "name": "Suresh Babu", "role": "BD Trainee",
     "email": "suresh.babu@edtech.example", "phone": "+919600011005"},
]
BD = {c["caller_id"]: {"id": c["caller_id"], "name": c["name"].split()[0]} for c in CALLERS_SPEC}

FULL_STACK = "Full Stack Development"
DATA_SCIENCE = "Data Science"
AI_ML = "AI/ML"
CLOUD = "Cloud Computing"
CYBER = "Cyber Security"
ANALYTICS = "Data Analytics"


def when(**kwargs) -> datetime:
    return NOW + timedelta(**kwargs)


def spoken(target: datetime) -> str:
    """Render a follow-up time the way a lead would say it on a call (in IST)."""
    local = target.astimezone(IST)
    return f"{local.day} {local:%B} at {local:%I:%M %p}".replace(" 0", " ")


# --------------------------------------------------------------------------
# Lead + call definitions
#
# Each call carries the transcript and the pre-baked analysis that a Gemini
# run over that transcript would produce. `target` is the follow-up moment
# the transcript asks for (None for converted / dropped / unscheduled).
# --------------------------------------------------------------------------

LEADS_SPEC = [
    # ---------------- OVERDUE ----------------
    {
        "lead_id": "L001", "name": "Arun Kumar", "phone": "+919840112233",
        "email": "arun.kumar@example.com", "course": FULL_STACK, "bd": "BD001",
        "created": when(days=-6), "expect": "OVERDUE",
        "calls": [{
            "caller": "BD001", "ended": when(days=-3, hours=-1), "duration": 504,
            "target": when(hours=-2),
            "text": ("BD: Hi Arun, this is Priya from the admissions team. Did you get a chance to look "
                     "at the Full Stack Development brochure?\n"
                     "Lead: Yes, I went through it. I am interested in the Full Stack Development course. "
                     "The curriculum looks good but I need to discuss the fees with my parents.\n"
                     "BD: Sure. Would it help if I shared the EMI options?\n"
                     "Lead: Yes please send them. Please call me on {when} once I have spoken to them."),
            "intent": "NEEDS_TIME", "confidence": 0.93,
            "summary": "Arun is interested in Full Stack Development but must discuss the fees with his "
                       "parents before committing. He asked for a callback at a specific time.",
            "key_points": ["Interested in Full Stack Development", "Needs to discuss fees with parents",
                           "Asked for EMI options", "Requested a callback at a specific time"],
            "reason": "Lead wants to discuss fees with parents and asked for a callback.",
        }],
    },
    {
        "lead_id": "L002", "name": "Divya S", "phone": "+919840223344",
        "email": "divya.s@example.com", "course": DATA_SCIENCE, "bd": "BD002",
        "created": when(days=-8), "expect": "OVERDUE",
        "calls": [{
            "caller": "BD002", "ended": when(days=-4), "duration": 372,
            "target": when(days=-1, hours=-3),
            "text": ("Lead: The Data Science syllabus looks good, especially the capstone projects. "
                     "I am travelling for work this week so I cannot decide right now.\n"
                     "BD: No problem Divya. When would be a good time to reconnect?\n"
                     "Lead: Please call me on {when}. I will have a clearer picture by then."),
            "intent": "INTERESTED", "confidence": 0.91,
            "summary": "Divya likes the Data Science syllabus but is travelling and deferred the decision "
                       "to a specific callback time.",
            "key_points": ["Positive about the Data Science syllabus", "Travelling this week",
                           "Requested callback at a specific date and time"],
            "reason": "Lead is travelling and asked to be called back at a set time.",
        }],
    },
    {
        "lead_id": "L003", "name": "Vignesh P", "phone": "+919840334455",
        "email": "vignesh.p@example.com", "course": CLOUD, "bd": "BD002",
        "created": when(days=-10), "expect": "OVERDUE",
        "calls": [
            {"caller": "BD002", "ended": when(days=-9), "duration": 128, "target": when(days=-6),
             "text": ("Lead: I just saw your message about the Cloud Computing program. I am in office "
                      "right now, can you call me on {when}?\nBD: Of course, I will call you then."),
             "intent": "INTERESTED", "confidence": 0.88,
             "summary": "Brief first contact; Vignesh asked to be called back at a specific time.",
             "key_points": ["Initial enquiry about Cloud Computing", "Busy at work, requested callback"],
             "reason": "Lead requested a callback at a specific time."},
            {"caller": "BD002", "ended": when(days=-6), "duration": 641, "target": when(days=-3, hours=-5),
             "text": ("Lead: Thanks for the detailed walkthrough. I want to compare the AWS and Azure "
                      "tracks before deciding, and check which one my company prefers.\n"
                      "BD: Both tracks share the first four modules, so you can switch later too.\n"
                      "Lead: Good to know. Get back to me on {when} and I will confirm the track."),
             "intent": "INTERESTED", "confidence": 0.9,
             "summary": "Vignesh is comparing the AWS and Azure tracks and checking his employer's "
                        "preference before choosing. He set a specific follow-up time.",
             "key_points": ["Comparing AWS vs Azure tracks", "Checking employer preference",
                            "Will confirm track at the follow-up"],
             "reason": "Lead is choosing between tracks and asked for a follow-up call."},
        ],
    },
    # ---------------- DUE TODAY ----------------
    {
        "lead_id": "L004", "name": "Sneha M", "phone": "+919840445566",
        "email": "sneha.m@example.com", "course": AI_ML, "bd": "BD001",
        "created": when(days=-4), "expect": "DUE",
        "calls": [{
            "caller": "BD001", "ended": when(hours=-5), "duration": 455, "target": when(minutes=45),
            "text": ("Lead: The AI/ML placement support sounds useful. I am in a meeting right now though.\n"
                     "BD: Understood Sneha, I will keep it short. Shall I call back later today?\n"
                     "Lead: Yes, please call me on {when}. I want to ask about the mentor sessions."),
            "intent": "INTERESTED", "confidence": 0.9,
            "summary": "Sneha is interested in AI/ML, particularly the placement support, and asked for a "
                       "short callback later the same day to discuss mentor sessions.",
            "key_points": ["Interested in AI/ML placement support", "Wants details on mentor sessions",
                           "Callback requested later today"],
            "reason": "Lead was in a meeting and asked for a callback later today.",
        }],
    },
    {
        "lead_id": "L005", "name": "Mohammed Irfan", "phone": "+919840556677",
        "email": "m.irfan@example.com", "course": CYBER, "bd": "BD004",
        "created": when(days=-5), "expect": "DUE",
        "calls": [{
            "caller": "BD004", "ended": when(hours=-7), "duration": 289, "target": when(minutes=95),
            "text": ("Lead: I want to know about the Cyber Security lab access and whether there are EMI "
                     "options. The upfront fee is a bit high for me.\n"
                     "BD: We do have a no-cost EMI plan. I can walk you through it.\n"
                     "Lead: Please call me on {when} and explain the EMI plan in detail."),
            "intent": "PRICE_SENSITIVE", "confidence": 0.92,
            "summary": "Irfan is interested in Cyber Security but finds the upfront fee high and wants the "
                       "EMI plan explained in a follow-up call.",
            "key_points": ["Asked about lab access", "Upfront fee is a concern", "Wants EMI plan explained"],
            "reason": "Lead asked for EMI details in a follow-up call.",
        }],
    },
    # ---------------- UPCOMING ----------------
    {
        "lead_id": "L006", "name": "Keerthana Iyer", "phone": "+919840667788",
        "email": "keerthana.iyer@example.com", "course": ANALYTICS, "bd": "BD003",
        "created": when(days=-3), "expect": "UPCOMING",
        "calls": [{
            "caller": "BD003", "ended": when(hours=-20), "duration": 612, "target": when(days=1, hours=2),
            "text": ("Lead: Data Analytics looks like the right fit for my career switch. I will discuss "
                     "with my manager about study leave and let you know.\n"
                     "BD: Great. When should I follow up?\n"
                     "Lead: Please call me on {when}."),
            "intent": "INTERESTED", "confidence": 0.9,
            "summary": "Keerthana sees Data Analytics as a fit for a career switch and will check study "
                       "leave with her manager before the agreed callback.",
            "key_points": ["Career switch into Data Analytics", "Checking study leave with manager",
                           "Callback scheduled"],
            "reason": "Lead is confirming study leave and asked for a callback.",
        }],
    },
    {
        "lead_id": "L007", "name": "Rohit S", "phone": "+919840778899",
        "email": "rohit.s@example.com", "course": FULL_STACK, "bd": "BD002",
        "created": when(days=-2), "expect": "UPCOMING",
        "calls": [{
            "caller": "BD002", "ended": when(hours=-30), "duration": 337, "target": when(days=1, hours=8),
            "text": ("Lead: I want to join the Full Stack Development batch, but I haven't made the payment "
                     "yet. My salary comes in on Friday.\n"
                     "BD: That's fine Rohit, the seat is held for a week.\n"
                     "Lead: Thanks. Please call me on {when} and I will complete the payment on the call."),
            "intent": "READY_TO_ENROLL", "confidence": 0.94,
            "summary": "Rohit has decided to join Full Stack Development but has not paid yet; he asked for "
                       "a callback to complete payment.",
            "key_points": ["Decided to join", "Payment pending until salary date",
                           "Callback scheduled to complete payment"],
            "reason": "Payment pending; lead asked for a callback to complete it.",
        }],
    },
    {
        "lead_id": "L008", "name": "Priyanka Menon", "phone": "+919840889900",
        "email": "priyanka.menon@example.com", "course": DATA_SCIENCE, "bd": "BD004",
        "created": when(days=-12), "expect": "UPCOMING",
        "calls": [{
            "caller": "BD004", "ended": when(days=-1, hours=-2), "duration": 218, "target": when(days=6, hours=3),
            "text": ("Lead: My notice period ends this month, so I am very busy with handover right now.\n"
                     "BD: Understood. Would you like me to reach out after that?\n"
                     "Lead: Yes, please call me on {when}. I do want to start Data Science before my new job."),
            "intent": "INTERESTED", "confidence": 0.89,
            "summary": "Priyanka is finishing her notice period and asked to be contacted next week, "
                       "intending to start Data Science before her new job.",
            "key_points": ["Busy with notice period handover", "Wants to start before new job",
                           "Callback next week"],
            "reason": "Lead is busy until end of notice period and asked for a callback.",
        }],
    },
    # ---------------- UNSCHEDULED (follow-up required, no date given) ----------------
    {
        "lead_id": "L014", "name": "Naveen B", "phone": "+919841445566",
        "email": "naveen.b@example.com", "course": FULL_STACK, "bd": "BD005",
        "created": when(days=-2), "expect": "UNSCHEDULED",
        "calls": [{
            "caller": "BD005", "ended": when(hours=-4), "duration": 402, "target": None,
            "text": ("Lead: The course looks interesting. Let me discuss it with my family and I will "
                     "get back to you.\nBD: Sure Naveen. Is there a good time for me to call?\n"
                     "Lead: I am not sure yet, I will let you know."),
            "intent": "NEEDS_TIME", "confidence": 0.87,
            "summary": "Naveen is interested in Full Stack Development but wants to discuss with family "
                       "first. He did not commit to a callback time.",
            "key_points": ["Interested in the course", "Wants to discuss with family",
                           "No callback time specified"],
            "reason": NO_DATE_REASON,
        }],
    },
    # ---------------- CONVERTED ----------------
    {
        "lead_id": "L009", "name": "Karthik R", "phone": "+919840990011",
        "email": "karthik.r@example.com", "course": AI_ML, "bd": "BD001",
        "created": when(days=-14), "expect": "CONVERTED",
        "calls": [{
            "caller": "BD001", "ended": when(hours=-26), "duration": 731, "target": None,
            "text": ("Lead: I have completed the payment and I would like to proceed with the AI/ML course.\n"
                     "BD: Congratulations Karthik! You will receive the onboarding mail within an hour.\n"
                     "Lead: Perfect, thank you."),
            "intent": "READY_TO_ENROLL", "confidence": 0.97,
            "summary": "Karthik confirmed payment is complete and wants to proceed with AI/ML. Enrollment "
                       "is done.",
            "key_points": ["Payment completed", "Confirmed enrollment in AI/ML", "Onboarding mail promised"],
        }],
    },
    {
        "lead_id": "L010", "name": "Aishwarya Nair", "phone": "+919841001122",
        "email": "aishwarya.nair@example.com", "course": DATA_SCIENCE, "bd": "BD003",
        "created": when(days=-11), "expect": "CONVERTED",
        "calls": [{
            "caller": "BD003", "ended": when(days=-2), "duration": 566, "target": None,
            "text": ("Lead: I have decided to join the Data Science program. Please send me the enrollment "
                     "details and the payment link.\nBD: Sending them right now, Aishwarya."),
            "intent": "READY_TO_ENROLL", "confidence": 0.95,
            "summary": "Aishwarya decided to join Data Science and asked for enrollment details and the "
                       "payment link.",
            "key_points": ["Decided to join Data Science", "Requested enrollment details and payment link"],
        }],
    },
    {
        "lead_id": "L011", "name": "Ramesh V", "phone": "+919841112233",
        "email": "ramesh.v@example.com", "course": CLOUD, "bd": "BD004",
        "created": when(days=-9), "expect": "CONVERTED",
        "calls": [
            {"caller": "BD004", "ended": when(days=-5), "duration": 402, "target": when(days=-3),
             "text": ("Lead: The Cloud Computing course fits my plan for a DevOps role. Please call me on "
                      "{when} after I check with my team lead."),
             "intent": "INTERESTED", "confidence": 0.9,
             "summary": "Ramesh finds Cloud Computing a fit and will confirm after speaking to his team lead.",
             "key_points": ["Targeting a DevOps role", "Checking with team lead", "Callback scheduled"],
             "reason": "Lead is checking with team lead and asked for a callback."},
            {"caller": "BD004", "ended": when(days=-3, hours=2), "duration": 295, "target": None,
             "text": ("Lead: My team lead is fine with it. I want to enroll in the Cloud Computing course. "
                      "I have paid the fees just now through the link.\nBD: Received, Ramesh. Welcome aboard!"),
             "intent": "READY_TO_ENROLL", "confidence": 0.96,
             "summary": "Ramesh confirmed enrollment in Cloud Computing and has paid the fees.",
             "key_points": ["Team lead approved", "Paid the fees", "Enrollment confirmed"]},
        ],
    },
    # ---------------- DROPPED ----------------
    {
        "lead_id": "L012", "name": "Anitha V", "phone": "+919841223344",
        "email": "anitha.v@example.com", "course": CYBER, "bd": "BD005",
        "created": when(days=-15), "expect": "DROPPED",
        "calls": [
            {"caller": "BD005", "ended": when(days=-8), "duration": 240, "target": when(days=-5),
             "text": ("Lead: I am considering Cyber Security but also looking at another institute. Call me "
                      "on {when} and I will tell you my decision."),
             "intent": "INTERESTED", "confidence": 0.85,
             "summary": "Anitha is comparing the Cyber Security course with another institute.",
             "key_points": ["Comparing with another institute", "Callback scheduled for decision"],
             "reason": "Lead is comparing options and asked for a callback."},
            {"caller": "BD005", "ended": when(days=-5, hours=1), "duration": 96, "target": None,
             "text": ("Lead: I have decided not to join the course. I am not interested anymore, I went with "
                      "the other institute. Please don't call me again.\nBD: Understood Anitha, all the best."),
             "intent": "NOT_INTERESTED", "confidence": 0.98,
             "summary": "Anitha explicitly declined the course, chose another institute and asked not to be "
                        "contacted.",
             "key_points": ["Chose another institute", "Not interested anymore", "Asked not to be called"]},
        ],
    },
    {
        "lead_id": "L013", "name": "Meena K", "phone": "+919841334455",
        "email": "meena.k@example.com", "course": ANALYTICS, "bd": "BD005",
        "created": when(days=-7), "expect": "DROPPED",
        "calls": [{
            "caller": "BD005", "ended": when(days=-1), "duration": 154, "target": None,
            "text": ("Lead: I am not interested in the Data Analytics course. My company is sponsoring an "
                     "internal program instead, so I don't want the course.\nBD: Thank you for letting me know."),
            "intent": "NOT_INTERESTED", "confidence": 0.96,
            "summary": "Meena is not interested; her employer is sponsoring an internal program instead.",
            "key_points": ["Not interested", "Employer sponsoring an internal program"],
        }],
    },
    # ---------------- NEW lead, no calls yet -- the demo target for "Analyze New Call" ----------------
    {
        "lead_id": "L015", "name": "Sandhya Rajan", "phone": "+919841556677",
        "email": "sandhya.rajan@example.com", "course": AI_ML, "bd": "BD003",
        "created": when(hours=-3), "expect": "NEW", "calls": [],
    },
]


def outcome_for(call: dict) -> Outcome:
    intent = call["intent"]
    if intent == "NOT_INTERESTED":
        return Outcome.DROPPED
    if intent == "READY_TO_ENROLL" and call["target"] is None:
        return Outcome.CONVERTED
    return Outcome.FOLLOW_UP_REQUIRED


def build_analysis_result(call: dict) -> CallAnalysisResult:
    """Pre-baked analysis, validated through the same gate as live LLM output."""
    outcome = outcome_for(call)
    payload = {
        "outcome": outcome.value,
        "follow_up_required": outcome is Outcome.FOLLOW_UP_REQUIRED,
        "follow_up": None,
        "customer_intent": call["intent"],
        "summary": call["summary"],
        "key_points": call["key_points"],
        "confidence": call["confidence"],
    }
    if outcome is Outcome.FOLLOW_UP_REQUIRED:
        target = call["target"]
        payload["follow_up"] = {
            "datetime": target.astimezone(IST).isoformat() if target else None,
            "reason": call.get("reason"),
        }
    return CallAnalysisResult.model_validate(payload)


def main() -> None:
    ping()
    db = get_db()

    for name in (LEADS, CALLERS, CALLS, CALL_TRANSCRIPTS, CALL_ANALYSES, "call_outcomes"):
        db.drop_collection(name)
    ensure_indexes()

    # ---- Callers ----
    db[CALLERS].insert_many(
        [{**c, "active": True, "created_at": when(days=-60)} for c in CALLERS_SPEC]
    )

    call_counter = 0
    leads_docs: list[dict] = []
    calls_docs: list[dict] = []
    transcripts_docs: list[dict] = []
    analyses_docs: list[dict] = []

    for spec in LEADS_SPEC:
        lead = {
            "lead_id": spec["lead_id"],
            "name": spec["name"],
            "phone": spec["phone"],
            "email": spec["email"],
            "course": spec["course"],
            "assigned_bd": BD[spec["bd"]],
            "lead_status": LeadStatus.NEW.value,
            "follow_up": dict(followup_service.NO_FOLLOW_UP),
            "follow_up_history": [],
            "last_call_at": None,
            "latest_call_id": None,
            "latest_outcome": None,
            "latest_analysis_id": None,
            "created_at": spec["created"],
            "updated_at": spec["created"],
        }
        leads_docs.append(lead)

        for call in spec["calls"]:
            call_counter += 1
            call_id = f"CALL-{call_counter:03d}"
            transcript_id = f"TR-{call_counter:03d}"
            analysis_id = f"AN-{call_counter:03d}"
            ended = call["ended"]
            text = call["text"].replace("{when}", spoken(call["target"])) if call["target"] else call["text"]

            result = build_analysis_result(call)

            calls_docs.append({
                "call_id": call_id,
                "lead_id": spec["lead_id"],
                "caller_id": call["caller"],
                "source_type": "UPLOAD",
                "audio_url": None,
                "audio_file_path": None,      # seeded calls have no recording on disk
                "audio_mime": None,
                "audio_bytes": None,
                "audio_filename": None,
                "duration_seconds": call["duration"],
                "status": CallStatus.COMPLETED.value,
                "error": None,
                "transcript_id": transcript_id,
                "analysis_id": analysis_id,
                "started_at": ended - timedelta(seconds=call["duration"]),
                "ended_at": ended,
                "created_at": ended,
                "processed_at": ended + timedelta(minutes=1),
                "seeded": True,
            })
            transcripts_docs.append({
                "transcript_id": transcript_id,
                "call_id": call_id,
                "lead_id": spec["lead_id"],
                "caller_id": call["caller"],
                "transcript": text,
                "language": "en-IN",
                "duration_seconds": call["duration"],
                "provider": SEED_MODEL,
                "created_at": ended + timedelta(seconds=30),
            })
            analyses_docs.append({
                "analysis_id": analysis_id,
                "call_id": call_id,
                "lead_id": spec["lead_id"],
                "caller_id": call["caller"],
                "model": SEED_MODEL,
                "status": "COMPLETED",
                "outcome": result.outcome.value,
                "follow_up_required": result.follow_up_required,
                "follow_up": result.follow_up.model_dump() if result.follow_up else None,
                "customer_intent": result.customer_intent.value,
                "summary": result.summary,
                "key_points": result.key_points,
                "confidence": result.confidence,
                "raw_response": None,
                "error": None,
                "degraded": False,
                "created_at": ended + timedelta(minutes=1),
            })

            # Project the analysis onto the lead exactly as the pipeline does,
            # in call order, so the LAST call wins.
            lead["lead_status"] = followup_service.OUTCOME_TO_LEAD_STATUS[result.outcome].value
            lead["follow_up"] = followup_service.follow_up_block_from_analysis(result)
            lead["last_call_at"] = ended
            lead["latest_call_id"] = call_id
            lead["latest_outcome"] = result.outcome.value
            lead["latest_analysis_id"] = analysis_id
            lead["updated_at"] = ended + timedelta(minutes=1)

    db[LEADS].insert_many(leads_docs)
    db[CALLS].insert_many(calls_docs)
    db[CALL_TRANSCRIPTS].insert_many(transcripts_docs)
    db[CALL_ANALYSES].insert_many(analyses_docs)

    # ---- Report ----
    print(f"Seeded at {NOW:%Y-%m-%d %H:%M} UTC\n")
    print(f"{'Lead':<6} {'Name':<18} {'Course':<24} {'BD':<8} {'Outcome':<19} {'Follow-up (UTC)':<18} Bucket")
    for spec in LEADS_SPEC:
        lead = db[LEADS].find_one({"lead_id": spec["lead_id"]})
        bucket = followup_service.compute_bucket(lead["follow_up"], NOW).value
        fu = lead["follow_up"]
        shown = fu["datetime"].strftime("%d %b %H:%M") if fu.get("datetime") else ("(no date)" if fu.get("required") else "-")
        expected = spec["expect"]
        actual = bucket if fu.get("required") else (lead["lead_status"] if lead["latest_outcome"] else "NEW")
        flag = "" if actual == expected else "  <-- unexpected"
        print(f"{lead['lead_id']:<6} {lead['name']:<18} {lead['course']:<24} {lead['assigned_bd']['name']:<8} "
              f"{lead['latest_outcome'] or '-':<19} {shown:<18} {actual}{flag}")

    print(
        f"\nCollections: leads={db[LEADS].count_documents({})} callers={db[CALLERS].count_documents({})} "
        f"calls={db[CALLS].count_documents({})} call_transcripts={db[CALL_TRANSCRIPTS].count_documents({})} "
        f"call_analyses={db[CALL_ANALYSES].count_documents({})}"
    )
    print("\nL015 Sandhya Rajan has no calls yet -- use her as the target for 'Analyze New Call'.")
    close_client()


if __name__ == "__main__":
    main()
