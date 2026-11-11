"""Types shared by the Signals judge and runner."""

from dataclasses import dataclass
from typing import Literal


@dataclass
class Finding:
    severity: Literal["info", "warning", "critical"]
    span_ids: list[str]
    fields: dict
    evidence_labels: list[str]
