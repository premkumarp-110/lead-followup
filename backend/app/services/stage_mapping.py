"""Map the CRM's free-text `stage` onto our coarse LeadStatus enum.

The CRM's stage is free text with ~30 observed values; LeadStatus has six.
Feeding a raw stage straight into the enum raises a ValidationError and rejects
a real record, so every stage goes through here and an unrecognised one degrades
to NEW with a single logged warning.

The raw stage is always stored alongside the mapped status. That matters because
of the **numbered ladders** -- `DNP 1..5`, `Follow-up 1..2`, `Not Responding 1..2`
encode attempt count in the stage name. They are a sequence, not independent
categories, and flattening `DNP 1` and `DNP 5` to the same thing throws away the
CRM's own escalation signal. `ladder_step()` recovers it.
"""

from __future__ import annotations

import logging
import re

from app.models.lead import LeadStatus

logger = logging.getLogger(__name__)

# Trailing digits are the ladder step, not part of the category.
_LADDER = re.compile(r"^(?P<base>.*?)\s*(?P<step>\d+)$")

# Keyed by the normalised stage (lowercased, ladder number stripped).
STAGE_TO_STATUS: dict[str, LeadStatus] = {
    # -- Converted ---------------------------------------------------------
    "converted": LeadStatus.CONVERTED,
    # -- Dropped -----------------------------------------------------------
    "not interested": LeadStatus.DROPPED,
    "junk": LeadStatus.DROPPED,
    "invalid": LeadStatus.DROPPED,
    "not looking for the course": LeadStatus.DROPPED,
    "did not apply": LeadStatus.DROPPED,
    "not eligible": LeadStatus.DROPPED,
    "not aware of the course": LeadStatus.DROPPED,
    # -- Follow-up wanted --------------------------------------------------
    "follow-up": LeadStatus.FOLLOW_UP,
    "follow up": LeadStatus.FOLLOW_UP,
    "likely to enroll": LeadStatus.FOLLOW_UP,
    "enroll later": LeadStatus.FOLLOW_UP,
    "payment link shared": LeadStatus.FOLLOW_UP,
    "agreed to enroll": LeadStatus.FOLLOW_UP,
    "qualified": LeadStatus.FOLLOW_UP,
    "exploring courses": LeadStatus.FOLLOW_UP,
    # -- Reached out, no outcome yet ---------------------------------------
    "dnp": LeadStatus.CONTACTED,
    "not responding": LeadStatus.CONTACTED,
    "not reachable": LeadStatus.CONTACTED,
    "language barrier": LeadStatus.CONTACTED,
    "reassigned": LeadStatus.CONTACTED,
    # -- Untouched ---------------------------------------------------------
    "new": LeadStatus.NEW,
}

# Stages whose whole point is that a follow-up was agreed. Used to decide
# whether an UNSCHEDULED follow-up should exist when the call analysis could not
# extract a datetime.
FOLLOW_UP_STAGES = {stage for stage, status in STAGE_TO_STATUS.items() if status is LeadStatus.FOLLOW_UP}

_warned: set[str] = set()


def _normalise(stage: str | None) -> tuple[str, int | None]:
    """Lowercase, trim, and split a trailing ladder number off the base name."""
    text = (stage or "").strip().lower()
    if not text:
        return "", None
    match = _LADDER.match(text)
    if match and match.group("base"):
        return match.group("base").strip(), int(match.group("step"))
    return text, None


def map_stage(stage: str | None) -> LeadStatus:
    """CRM stage -> LeadStatus. Never raises; unknown stages become NEW."""
    base, _ = _normalise(stage)
    if not base:
        return LeadStatus.NEW

    status = STAGE_TO_STATUS.get(base)
    if status is not None:
        return status

    if base not in _warned:
        _warned.add(base)
        logger.warning(
            "Unrecognised CRM stage %r -> defaulting to NEW. Add it to STAGE_TO_STATUS "
            "in services/stage_mapping.py if it should map elsewhere.",
            stage,
        )
    return LeadStatus.NEW


def ladder_step(stage: str | None) -> int | None:
    """The attempt number encoded in a stage name, e.g. 'DNP 5' -> 5.

    None when the stage carries no number. A DNP 5 lead deserves different
    treatment from a DNP 1, and this is the only place that signal survives.
    """
    _, step = _normalise(stage)
    return step


def is_follow_up_stage(stage: str | None) -> bool:
    base, _ = _normalise(stage)
    return base in FOLLOW_UP_STAGES


def known_stages() -> list[str]:
    """Base stage names we recognise, for documentation and tests."""
    return sorted(STAGE_TO_STATUS)
