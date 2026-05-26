from __future__ import annotations

import json
from typing import Any

import aiohttp

from kestrel.types import BrowserState, Action
from kestrel.logging import log_event


class LLMClient:
    def __init__(
        self,
        model: str = "llama3.2:3b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.0,
    ):
        self.model = model
        self.base_url = base_url
        self.temperature = temperature

    async def decide(
        self,
        state: BrowserState,
        goal: str,
        validators: list[dict[str, Any]],
        hints: list[str],
        actions: list[str],
        history: list[tuple[Action, str | None]],
        current_action: str | None = None,
    ) -> str:
        """Send state to LLM and return raw JSON string."""
        prompt = self._build_prompt(
            state,
            goal,
            validators,
            hints,
            actions,
            history,
            current_action=current_action,
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": self.temperature,
            },
            "format": "json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/api/chat",
                json=payload,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                content = data.get("message", {}).get("content", "")
                # Some models return markdown fences; strip them
                content = content.strip()
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                content = content.strip()
                log_event("debug", "LLM response", {"raw": content})
                return content

    def _system_prompt(self) -> str:
        return (
            "You are an autonomous browser testing agent. "
            "Your job is to navigate a web application and achieve a goal.\n\n"
            "RULES:\n"
            "1. Respond ONLY with a single JSON object. No prose, no explanations.\n"
            "2. Choose exactly ONE action at a time.\n"
            "3. Do not repeat the same failed action on the same element.\n"
            "4. Prefer clicking buttons/links by their visible text.\n"
            "5. If an element is missing, try an alternative approach.\n"
            "6. Avoid unnecessary navigation.\n"
            "7. You may NOT mark success yourself; only the validator engine decides pass/fail.\n"
            "8. When filling forms: type each unfilled field exactly once, then click the submit button. Do not type into a field that is already filled.\n"
            "9. CRITICAL: Pay attention to 'filled inputs' — they already have content. Never type into a field listed there. If all visible inputs are filled, click the submit button.\n\n"
            "JSON FORMAT:\n"
            '- click:   {"action": "click", "target": "button text"}\n'
            '- type:    {"action": "type", "target": "field label", "text": "value"}\n'
            '- goto:    {"action": "goto", "url": "http://..."}\n'
            '- wait:    {"action": "wait"}\n'
            '- done:    {"action": "done"}\n'
        )

    def _build_prompt(
        self,
        state: BrowserState,
        goal: str,
        validators: list[dict[str, Any]],
        hints: list[str],
        actions: list[str],
        history: list[tuple[Action, str | None]],
        current_action: str | None = None,
    ) -> str:
        lines: list[str] = []
        if current_action:
            lines.append("=== YOUR TASK ===")
            lines.append(current_action)
            lines.append("=== END TASK ===")
            lines.append("")
        lines.append(f"GOAL: {goal}")
        lines.append("")
        lines.append("VALIDATORS:")
        for v in validators:
            lines.append(f"  - {json.dumps(v)}")
        lines.append("")
        if hints:
            lines.append("HINTS:")
            for h in hints:
                lines.append(f"  - {h}")
            lines.append("")

        lines.append("CURRENT STATE:")
        lines.append(f"  URL: {state.url}")
        lines.append(f"  Title: {state.title}")
        if state.buttons:
            lines.append(f"  Buttons: {state.buttons[:15]}")
        if state.filled_inputs:
            lines.append(
                f"  Filled inputs (do NOT type into these): {state.filled_inputs[:15]}"
            )
        unfilled = [i for i in state.inputs if i not in state.filled_inputs]
        if unfilled:
            lines.append(f"  Inputs (needs typing): {unfilled[:15]}")
        if state.links:
            lines.append(f"  Links: {state.links[:15]}")
        if state.visible_text:
            lines.append(f"  Visible text snippets: {state.visible_text[:20]}")
        if state.console_errors:
            lines.append(f"  Console errors: {state.console_errors[:5]}")
        if state.network_failures:
            lines.append(f"  Network failures: {state.network_failures[:5]}")
        lines.append("")

        if history:
            lines.append("ACTION HISTORY (most recent last):")
            for idx, (act, err) in enumerate(history[-10:], 1):
                err_str = f" [ERROR: {err}]" if err else ""
                text_str = f' "{act.text}" into' if act.text else ""
                lines.append(
                    f"  {idx}. {act.action}{text_str}{f' {act.target}' if act.target else ''}{err_str}"
                )
            lines.append("")

        lines.append("Return exactly one JSON action.")
        return "\n".join(lines)
