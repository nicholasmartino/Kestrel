from __future__ import annotations

import json
from typing import Any

from kestrel.types import Action


class ActionError(Exception):
    pass


def parse_action(raw: str) -> Action:
    """Parse JSON action from the LLM, leniently mapping common key aliases."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ActionError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ActionError("Action must be a JSON object")

    action_type = data.get("action") or data.get("type") or "wait"
    target = data.get("target") or data.get("element")
    text = data.get("text")
    url = data.get("url")

    return Action(action=action_type, target=target, text=text, url=url)


def action_to_prompt_example(action: Action) -> str:
    return json.dumps(action.to_dict())
