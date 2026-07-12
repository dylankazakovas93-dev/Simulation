from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ContractStatus(StrEnum):
    ENABLED = "enabled"
    SOURCE_GAP = "source_gap"
    DISABLED = "disabled"


@dataclass(frozen=True)
class SourceReference:
    document: str
    sha256: str
    pages: str


@dataclass(frozen=True)
class RuleContract:
    """A reviewable rule record, intentionally independent of simulation state."""

    id: str
    firm: str
    program: str
    stage: str
    account_name: str
    status: ContractStatus
    mechanics: dict[str, Any]
    sources: tuple[SourceReference, ...]
    notes: tuple[str, ...] = ()

    @property
    def profile_key(self) -> str:
        return f"{self.firm} - {self.account_name}"
