from __future__ import annotations

import fnmatch
from typing import Any, Callable

from kestrel.types import BrowserState, ValidatorResult

ValidatorFn = Callable[[BrowserState, Any], ValidatorResult]

_registry: dict[str, ValidatorFn] = {}


def register(name: str) -> Callable[[ValidatorFn], ValidatorFn]:
    def decorator(fn: ValidatorFn) -> ValidatorFn:
        _registry[name] = fn
        return fn

    return decorator


def evaluate(state: BrowserState, validator_def: dict[str, Any]) -> ValidatorResult:
    if len(validator_def) != 1:
        return ValidatorResult(
            name=str(validator_def),
            passed=False,
            detail="Validator must have exactly one key",
        )
    name, arg = next(iter(validator_def.items()))
    fn = _registry.get(name)
    if fn is None:
        return ValidatorResult(
            name=name,
            passed=False,
            detail=f"Unknown validator: {name}",
        )
    return fn(state, arg)


@register("url_contains")
def _url_contains(state: BrowserState, arg: Any) -> ValidatorResult:
    needle = str(arg)
    passed = needle in state.url
    return ValidatorResult(
        name=f"url_contains:{needle}",
        passed=passed,
        detail=f"URL '{state.url}' {'contains' if passed else 'does not contain'} '{needle}'",
    )


@register("text_visible")
def _text_visible(state: BrowserState, arg: Any) -> ValidatorResult:
    needle = str(arg).lower()
    all_text = " ".join(state.visible_text).lower()
    passed = needle in all_text
    return ValidatorResult(
        name=f"text_visible:{arg}",
        passed=passed,
        detail=f"Text '{arg}' {'is' if passed else 'is not'} visible",
    )


@register("button_visible")
def _button_visible(state: BrowserState, arg: Any) -> ValidatorResult:
    needle = str(arg).lower()
    all_buttons = " ".join(state.buttons).lower()
    passed = needle in all_buttons
    return ValidatorResult(
        name=f"button_visible:{arg}",
        passed=passed,
        detail=f"Button '{arg}' {'is' if passed else 'is not'} visible",
    )


@register("no_console_errors")
def _no_console_errors(state: BrowserState, _arg: Any) -> ValidatorResult:
    passed = len(state.console_errors) == 0
    return ValidatorResult(
        name="no_console_errors",
        passed=passed,
        detail=(
            f"{len(state.console_errors)} console error(s) found"
            if not passed
            else "No console errors"
        ),
    )


@register("network_status")
def _network_status(state: BrowserState, arg: Any) -> ValidatorResult:
    # Expected format: "POST /checkout = 200" or "GET /api = 2xx"
    expr = str(arg)
    parts = expr.split("=")
    if len(parts) != 2:
        return ValidatorResult(
            name=f"network_status:{expr}",
            passed=False,
            detail="Expected format: 'METHOD /path = status'",
        )
    left = parts[0].strip()
    expected_status = parts[1].strip()
    method_path = left.split(" ", 1)
    if len(method_path) != 2:
        return ValidatorResult(
            name=f"network_status:{expr}",
            passed=False,
            detail="Expected format: 'METHOD /path = status'",
        )
    expected_method, expected_path = method_path

    for req in state.network_requests:
        # requests stored as "METHOD URL status"
        rparts = req.split(" ", 2)
        if len(rparts) < 3:
            continue
        r_method, r_url, r_status = rparts[0], rparts[1], rparts[2]
        if r_method.upper() == expected_method.upper() and fnmatch.fnmatch(
            r_url, f"*{expected_path}*"
        ):
            if not fnmatch.fnmatch(r_status, expected_status):
                return ValidatorResult(
                    name=f"network_status:{expr}",
                    passed=False,
                    detail=f"Request matched but status was {r_status}, expected {expected_status}",
                )
            return ValidatorResult(
                name=f"network_status:{expr}",
                passed=True,
                detail=f"Request matched with status {r_status}",
            )

    return ValidatorResult(
        name=f"network_status:{expr}",
        passed=False,
        detail="No matching network request found",
    )
