from __future__ import annotations

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
                    history=self._history,
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
                    # Evaluate validators one last time
                    final_state = step_result.state_after or state
                    validator_results = self._evaluate_validators(final_state)
                    all_passed = all(v.passed for v in validator_results)
                    return self._make_result(
                        steps,
                        passed=all_passed,
                        validators=validator_results,
                        start_time=start_time,
                        error=None if all_passed else "Validators failed after done",
                    )

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
