"""Regression check for the deterministic (fallback) analyzer.

Run with: .venv/bin/python check_analyzer.py
Covers the spec's sample transcripts plus edge cases. No network access needed.
"""

from datetime import datetime, timezone

from app.services.call_analyzer import analyze_call

REFERENCE = datetime(2026, 9, 21, 10, 28, tzinfo=timezone.utc)

# (transcript, expected outcome, expected follow-up "YYYY-MM-DD HH:MM" or None)
CASES = [
    ("I am interested in the Full Stack Development course. I need to discuss the fees "
     "with my parents. Please call me tomorrow at 11 AM.", "FOLLOW_UP_REQUIRED", "2026-09-22 11:00"),
    ("I have completed the payment and I would like to proceed with the course.", "CONVERTED", None),
    ("I have decided not to join the course. I am not interested anymore.", "DROPPED", None),
    ("The course looks good. I am currently busy. Please call me next week.",
     "FOLLOW_UP_REQUIRED", "2026-09-28 10:00"),
    ("I have decided to join the Data Science program. Please send me the enrollment details.",
     "CONVERTED", None),
    # Spec S27: follow-up WITHOUT a date -> required, but unscheduled (no invented date)
    ("The course looks interesting. Let me discuss it with my family and I will get back to you.",
     "FOLLOW_UP_REQUIRED", None),
    # Spec S27: payment follow-up -> still a follow-up, not converted
    ("I want to join, but I haven't made the payment yet. Please call me tomorrow evening.",
     "FOLLOW_UP_REQUIRED", "2026-09-22 18:00"),
    # Edge cases
    ("I want to enroll but I am not interested in the weekend batch, cancel that.", "DROPPED", None),
    ("Please call me on 2026-09-25 at 2:30 pm.", "FOLLOW_UP_REQUIRED", "2026-09-25 14:30"),
    ("Sounds fine, I will think about it.", "FOLLOW_UP_REQUIRED", None),
    ("", "FOLLOW_UP_REQUIRED", None),
    # --- CRM transcript format -------------------------------------------
    # Real transcripts are "[MM:SS] Agent:/Customer:" lines. The [MM:SS] is an
    # offset into the recording, NOT a clock time -- but it looks exactly like
    # one and appears before anything the speaker said, so without stripping it
    # every call gets scheduled at the first timestamp in the file.
    ("[00:00] Customer: Hello.\n"
     "[00:45] Agent: I will send the brochure.\n"
     "[01:20] Customer: Send the details, and call me back at 8 PM today.\n"
     "[01:30] Agent: Noted, I will call you at 8 in the evening.",
     "FOLLOW_UP_REQUIRED", "2026-09-21 20:00"),
    ("[00:05] Customer: Okay, set up a Google Meet tomorrow at 11 AM.\n"
     "[00:18] Agent: Done, I will send the invite.",
     "FOLLOW_UP_REQUIRED", "2026-09-22 11:00"),
    # Speaker labels without timestamps must still be stripped.
    ("Lead: I have completed the payment, please confirm.\n"
     "BD: Received, thank you.", "CONVERTED", None),
]

# Romanized Tamil/Malayalam transcripts must be REFUSED by the guard rather
# than silently falling through to the default outcome and being written to a
# lead as though it were a real finding.
LANGUAGE_GUARD_CASES = [
    ("[00:00] Agent: ok, kelkaam.\n"
     "[00:10] Agent: naan thaan ungalukku ennudaya personal number text onnu pottirunthen.\n"
     "[00:15] Customer: ippo vendaam, naan paarkala.\n"
     "[00:20] Agent: aama, onnum problem illai, konjam detail sollattaa?\n"
     "[00:32] Customer: saar, idhu placement eppadinna, ippo mark interview kuduppaanga?",
     False),
    ("[00:01] Agent: Hi, this is a call from HCL GUVI about your enquiry.\n"
     "[00:12] Customer: Yes, I am looking for a data analyst course, what are the fees?\n"
     "[00:25] Agent: I will share the fee structure and the EMI options with you today.\n"
     "[00:40] Customer: Okay please call me back tomorrow, I will discuss with my family.",
     True),
]


def check_sentiment() -> tuple[int, int]:
    """The fallback must never claim to have judged tone.

    It matches literal English phrases. A sentiment score derived from those
    same tables would be a guess dressed as a measurement -- and it gets
    written to the lead and used to order the worklist. UNKNOWN with no score
    is the only honest answer here.
    """
    from app.models.analysis import CallAnalysisResult, SentimentLabel, SentimentTrajectory

    checks: list[tuple[str, bool]] = []
    for transcript, _outcome, _when in CASES:
        result = analyze_call(transcript, call_ended_at=REFERENCE)
        s = result.sentiment
        checks.append((
            f"fallback stays UNKNOWN | {transcript[:44]!r}",
            s.label is SentimentLabel.UNKNOWN
            and s.score is None
            and s.trajectory is SentimentTrajectory.UNKNOWN,
        ))

    # An analysis stored before sentiment existed must still deserialise.
    legacy = CallAnalysisResult.model_validate(
        {"outcome": "DROPPED", "customer_intent": "NOT_INTERESTED", "confidence": 0.9}
    )
    checks.append(
        ("legacy payload with no sentiment key still validates",
         legacy.sentiment.label is SentimentLabel.UNKNOWN),
    )
    # ...and UNKNOWN must never carry a score, however it was supplied.
    forced = CallAnalysisResult.model_validate(
        {"outcome": "DROPPED", "sentiment": {"label": "UNKNOWN", "score": 0.9}}
    )
    checks.append(("UNKNOWN drops any supplied score", forced.sentiment.score is None))

    failures = 0
    for label, ok in checks:
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    return failures, len(checks)


def check_language_guard() -> int:
    from app.services.call_analyzer import is_analyzable_language

    failures = 0
    for transcript, expected in LANGUAGE_GUARD_CASES:
        actual = is_analyzable_language(transcript)
        ok = actual is expected
        failures += 0 if ok else 1
        label = "analyzable" if expected else "refused"
        print(f"[{'PASS' if ok else 'FAIL'}] language guard -> {label:10} | "
              f"{transcript.splitlines()[0][:52]}")
    return failures


def main() -> int:
    failures = 0
    # Every check counts toward the total, not just the CASES loop -- the
    # previous version added guard failures to the numerator while sizing the
    # denominator from len(CASES), so a failing guard printed e.g. "13/14".
    total = 0

    guard_failures = check_language_guard()
    failures += guard_failures
    total += len(LANGUAGE_GUARD_CASES)

    sentiment_failures, sentiment_total = check_sentiment()
    failures += sentiment_failures
    total += sentiment_total

    for transcript, expected_outcome, expected_when in CASES:
        result = analyze_call(transcript, call_ended_at=REFERENCE)
        actual_when = (
            result.follow_up.datetime.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            if result.follow_up and result.follow_up.datetime
            else None
        )
        ok = result.outcome.value == expected_outcome and actual_when == expected_when
        failures += 0 if ok else 1
        total += 1
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {result.outcome.value:<19} {str(actual_when):<17} "
              f"intent={result.customer_intent.value:<15} | {transcript[:60]!r}")
        if not ok:
            print(f"       expected {expected_outcome} @ {expected_when}")
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
