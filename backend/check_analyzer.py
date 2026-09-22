"""Quick sanity check for the deterministic analyzer.

Run with: .venv/bin/python check_analyzer.py
Covers the five sample transcripts from the spec plus the edge cases.
"""

from datetime import datetime, timezone

from app.services.call_analyzer import analyze_call

REFERENCE = datetime(2026, 9, 21, 10, 28, tzinfo=timezone.utc)

CASES = [
    ("I am interested in the Full Stack Development course. I need to discuss the fees "
     "with my parents. Please call me tomorrow at 11 AM.", "FOLLOW_UP_REQUIRED", "2026-09-22 11:00"),
    ("I have completed the payment and I want to proceed with the course.", "CONVERTED", None),
    ("I am not interested in the course anymore. Please don't contact me again.", "DROPPED", None),
    ("The course looks good. I am currently busy. Please call me next week.",
     "FOLLOW_UP_REQUIRED", "2026-09-28 10:00"),
    ("I have decided to join the Data Science program. Please send me the enrollment details.",
     "CONVERTED", None),
    # Edge cases
    ("I want to enroll but I am not interested in the weekend batch, cancel that.", "DROPPED", None),
    ("Please call me on 2026-09-25 at 2:30 pm.", "FOLLOW_UP_REQUIRED", "2026-09-25 14:30"),
    ("Sounds fine, I will think about it.", "FOLLOW_UP_REQUIRED", "2026-09-22 10:00"),
    ("", "FOLLOW_UP_REQUIRED", "2026-09-22 10:00"),
]


def main() -> int:
    failures = 0
    for transcript, expected_outcome, expected_when in CASES:
        result = analyze_call(transcript, context={"call_ended_at": REFERENCE})
        actual_when = (
            result.follow_up_datetime.strftime("%Y-%m-%d %H:%M")
            if result.follow_up_datetime
            else None
        )
        ok = result.outcome.value == expected_outcome and actual_when == expected_when
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {result.outcome.value:<19} "
              f"follow_up={str(actual_when):<17} conf={result.confidence:.2f}  "
              f"{transcript[:52] or '(empty transcript)'!r}")
        if not ok:
            print(f"        expected outcome={expected_outcome} follow_up={expected_when}")

    print()
    print(f"{len(CASES) - failures}/{len(CASES)} analyzer cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
