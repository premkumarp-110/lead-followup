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
]


def main() -> int:
    failures = 0
    for transcript, expected_outcome, expected_when in CASES:
        result = analyze_call(transcript, call_ended_at=REFERENCE)
        actual_when = (
            result.follow_up.datetime.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
            if result.follow_up and result.follow_up.datetime
            else None
        )
        ok = result.outcome.value == expected_outcome and actual_when == expected_when
        failures += 0 if ok else 1
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {result.outcome.value:<19} {str(actual_when):<17} "
              f"intent={result.customer_intent.value:<15} | {transcript[:60]!r}")
        if not ok:
            print(f"       expected {expected_outcome} @ {expected_when}")
    total = len(CASES)
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
