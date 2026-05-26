from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from kestrel.types import (
    Spec,
    BrowserState,
    Action,
    StepResult,
    AgentResult,
    ValidatorResult,
)
from kestrel.browser import BrowserManager
from kestrel.llm import LLMClient
from kestrel.actions import parse_action, ActionError
from kestrel import validators
from kestrel.logging import log_event


class Agent:
    def __init__(
        self,
        spec: Spec,
        browser: BrowserManager,
        llm: LLMClient,
        headless: bool = True,
    ):
        self.spec = spec
        self.browser = browser
        self.llm = llm
        self.headless = headless
        self._history: list[tuple[Action, str | None]] = []
        self._loop_hashes: list[str] = []
        self._step = 0
        self._action_index = 0

    async def run(self) -> AgentResult:
        start_time = time.monotonic()
        steps: list[StepResult] = []

        try:
            await self.browser.start()

            # Auto-navigate to base_url if provided
            if self.spec.base_url:
                error = await self.browser.execute(
                    Action(action="goto", url=self.spec.base_url)
                )
                if error:
                    return self._make_result(
                        steps,
                        passed=False,
                        error=f"Failed to navigate to base_url: {error}",
                        start_time=start_time,
                    )

            while self._step < self.spec.max_steps:
                elapsed = time.monotonic() - start_time
                if elapsed > self.spec.timeout_seconds:
                    return self._make_result(
                        steps,
                        passed=False,
                        error="Timeout exceeded",
                        start_time=start_time,
                    )

                state = await self.browser.extract_state()

                # Auto-wait if sign-in form hasn't rendered yet
                has_form_controls = any(
                    b.lower() in ("continue", "sign in", "submit", "log in")
                    for b in state.buttons
                )
                if not state.inputs and not has_form_controls:
                    action = Action(action="wait")
                    steps.append(StepResult(step=self._step, action=action, state_before=state))
                    await self.browser.execute(action)
                    self._history.append((action, None))
                    self._step += 1
                    continue

                # Evaluate validators after each step (except before first action)
                if self._step > 0:
                    validator_results = self._evaluate_validators(state)
                    all_passed = all(v.passed for v in validator_results)
                    if all_passed:
                        return self._make_result(
                            steps,
                            passed=True,
                            validators=validator_results,
                            start_time=start_time,
                        )

                # Determine current action (progressive disclosure)
                has_actions = bool(self.spec.actions)
                current_action = (
                    self.spec.actions[self._action_index]
                    if has_actions and self._action_index < len(self.spec.actions)
                    else None
                )

                # All actions consumed → buffer → validators
                if current_action is None and has_actions:
                    return await self._finish_with_buffer(steps, state, start_time)

                # Check for loops
                loop_detected = self._detect_loop(state)
                if loop_detected:
                    return self._make_result(
                        steps,
                        passed=False,
                        error="Loop detected",
                        start_time=start_time,
                    )

                # Get next action from LLM
                raw_action = await self.llm.decide(
                    state=state,
                    goal=self.spec.goal,
                    validators=self.spec.validators,
                    hints=self.spec.hints,
                    actions=self.spec.actions,
                    history=self._history,
                    current_action=current_action,
                )

                try:
                    action = parse_action(raw_action)
                except ActionError as exc:
                    self._history.append(
                        (Action(action="wait"), f"Invalid action JSON: {exc}")
                    )
                    steps.append(
                        StepResult(
                            step=self._step,
                            action=Action(action="wait"),
                            state_before=state,
                            error=str(exc),
                        )
                    )
                    self._step += 1
                    continue

                # Record step
                step_result = StepResult(
                    step=self._step,
                    action=action,
                    state_before=state,
                )

                # Execute action
                error = await self.browser.execute(action)
                step_result.error = error
                step_result.state_after = await self.browser.extract_state()
                steps.append(step_result)

                self._history.append((action, error))
                self._step += 1

                if action.action == "done":
                    if current_action:
                        log_event("warn", "Premature done, re-prompting", {"pending_action": current_action})
                        continue
                    return await self._finish_with_buffer(steps, step_result.state_after or state, start_time)

                # Advance action index if LLM's action type matches the task prefix
                if current_action and self._should_advance(current_action, action):
                    self._action_index += 1
                    log_event("debug", "Action advanced", {"index": self._action_index, "next": self.spec.actions[self._action_index] if self._action_index < len(self.spec.actions) else None})

            # Max steps reached
            final_state = await self.browser.extract_state()
            validator_results = self._evaluate_validators(final_state)
            all_passed = all(v.passed for v in validator_results)
            return self._make_result(
                steps,
                passed=all_passed,
                validators=validator_results,
                start_time=start_time,
                error="Max steps reached" if not all_passed else None,
            )

        except Exception as exc:
            return self._make_result(
                steps,
                passed=False,
                error=f"Agent exception: {exc}",
                start_time=start_time,
            )
        finally:
            await self.browser.stop()

    def _evaluate_validators(self, state: BrowserState) -> list[ValidatorResult]:
        results: list[ValidatorResult] = []
        for vdef in self.spec.validators:
            results.append(validators.evaluate(state, vdef))
        return results

    def _detect_loop(self, state: BrowserState) -> bool:
        if not self._history:
            return False
        last_action = self._history[-1][0]
        h = hashlib.md5(
            f"{state.url}:{last_action.action}:{last_action.target}".encode()
        ).hexdigest()
        self._loop_hashes.append(h)
        # Loop if same hash appears 3+ times in last 6 steps
        if len(self._loop_hashes) >= 6:
            recent = self._loop_hashes[-6:]
            for candidate in set(recent):
                if recent.count(candidate) >= 3:
                    return True
        return False

    def _should_advance(self, task_text: str, llm_action: Action) -> bool:
        prefix = task_text.strip().split(maxsplit=1)[0] if task_text else ""
        if prefix in ("type", "click", "navigate"):
            return llm_action.action == prefix
        return True

    async def _finish_with_buffer(
        self,
        steps: list[StepResult],
        state: BrowserState,
        start_time: float,
    ) -> AgentResult:
        if self.spec.buffer:
            log_event("info", "Buffer started", {"timeout": self.spec.buffer.timeout, "until": self.spec.buffer.until})
            deadline = time.monotonic() + self.spec.buffer.timeout
            while time.monotonic() < deadline:
                state = await self.browser.extract_state()
                if self.spec.buffer.until:
                    result = validators.evaluate(state, self.spec.buffer.until)
                    if result.passed:
                        log_event("info", "Buffer condition met", {"detail": result.detail})
                        break
                await asyncio.sleep(1)
            else:
                log_event("info", "Buffer timed out", {})
        validator_results = self._evaluate_validators(state)
        all_passed = all(v.passed for v in validator_results)
        return self._make_result(
            steps,
            passed=all_passed,
            validators=validator_results,
            start_time=start_time,
            error=None if all_passed else "Validators failed",
        )

    def _make_result(
        self,
        steps: list[StepResult],
        passed: bool,
        validators: list[ValidatorResult] | None = None,
        start_time: float = 0,
        error: str | None = None,
    ) -> AgentResult:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        return AgentResult(
            spec_name="spec",
            goal=self.spec.goal,
            passed=passed,
            validators=validators or [],
            steps=steps,
            total_steps=len(steps),
            duration_ms=duration_ms,
            error=error,
        )
