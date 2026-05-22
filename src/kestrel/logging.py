from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any

from kestrel.types import AgentResult


def log_event(level: str, message: str, extra: dict[str, Any] | None = None) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
    }
    if extra:
        entry.update(extra)
    print(json.dumps(entry), file=sys.stderr)


def print_result(result: AgentResult) -> None:
    out = {
        "spec": result.spec_name,
        "goal": result.goal,
        "passed": result.passed,
        "duration_ms": result.duration_ms,
        "total_steps": result.total_steps,
        "error": result.error,
        "validators": [
            {"name": v.name, "passed": v.passed, "detail": v.detail}
            for v in result.validators
        ],
        "steps": [
            {
                "step": s.step,
                "action": s.action.to_dict(),
                "error": s.error,
                "state_before": s.state_before.to_dict() if s.state_before else None,
            }
            for s in result.steps
        ],
    }
    print(json.dumps(out, indent=2))
