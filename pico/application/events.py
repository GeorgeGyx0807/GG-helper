"""Stable event envelope exposed to desktop and API clients."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class RunEvent:
    event_type: str
    run_id: str
    session_id: str
    sequence: int
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: "event_" + uuid4().hex)
    created_at: str = field(default_factory=_utc_now)

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "sequence": self.sequence,
            "created_at": self.created_at,
            "payload": dict(self.payload),
        }
