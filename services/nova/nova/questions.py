"""Audience questions. Nova reads only what a human moderator approved: passing the
automatic screen for kindness and personal details is not enough on its own. The screen
(nova/guardrails.py, screen_question) warns the moderator before they approve, and on air
it's a silent backstop: a question it catches is skipped, never declined aloud."""

from dataclasses import dataclass
from enum import StrEnum


class OffLimits(StrEnum):
    """Why the screen would hold an audience question back: the console's warning before a
    moderator approves it, and the code when one is skipped on air. Never the text."""

    PERSONAL_DETAIL = "personal_detail"  # where someone works, lives or studies, contacts
    LOOKS = "looks"
    INSTRUCTS_NOVA = "instructs_nova"


class QuestionStatus(StrEnum):
    SUBMITTED = "submitted"  # sent by a listener, not yet screened
    SCREENED = "screened"  # passed the automatic screen, waiting for a moderator
    APPROVED = "approved"  # approved by a moderator: the only status Nova reads
    REJECTED = "rejected"


@dataclass(frozen=True)
class AudienceQuestion:
    text: str
    status: QuestionStatus

    @property
    def approved(self) -> bool:
        return self.status is QuestionStatus.APPROVED
