from __future__ import annotations

import json
from typing import Any

from src.types import Action


class ActionError(Exception):
    pass


def parse_action(raw: str) -> Action:
    """Parse and validate a JSON action from the LLM."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ActionError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ActionError("Action must be a JSON object")

    action_type = data.get("action")
    if action_type not in ("goto", "click", "type", "wait", "done"):
        raise ActionError(f"Unsupported action: {action_type}")

    target = data.get("target")
    text = data.get("text")
    url = data.get("url")

    if action_type == "goto" and not url:
        raise ActionError("goto action requires 'url'")
    if action_type in ("click", "type") and not target:
        raise ActionError(f"{action_type} action requires 'target'")
    if action_type == "type" and text is None:
        raise ActionError("type action requires 'text'")

    return Action(
        action=action_type,
        target=target,
        text=text,
        url=url,
    )


def action_to_prompt_example(action: Action) -> str:
    return json.dumps(action.to_dict())
