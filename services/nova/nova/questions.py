"""Audience questions. Nova reads only what a human moderator approved: passing the
automatic screen for kindness and personal details is not enough on its own."""

from dataclasses import dataclass
from enum import StrEnum


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
