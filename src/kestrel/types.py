from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Buffer:
    timeout: float = 30.0
    until: dict | None = None


@dataclass(frozen=True)
class Action:
    action: str
    target: str | None = None
    text: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"action": self.action}
        if self.target is not None:
            d["target"] = self.target
        if self.text is not None:
            d["text"] = self.text
        if self.url is not None:
            d["url"] = self.url
        return d


@dataclass
class BrowserState:
    url: str
    title: str
    visible_text: list[str]
    buttons: list[str]
    inputs: list[str]
    links: list[str]
    console_errors: list[str]
    network_failures: list[str]
    network_requests: list[str]
    filled_inputs: list[str] = field(default_factory=list)
    accessibility_tree: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "visible_text": self.visible_text,
            "buttons": self.buttons,
            "inputs": self.inputs,
            "filled_inputs": self.filled_inputs,
            "links": self.links,
            "console_errors": self.console_errors,
            "network_failures": self.network_failures,
            "network_requests": self.network_requests,
        }


@dataclass
class ValidatorResult:
    name: str
    passed: bool
    detail: str


@dataclass
class StepResult:
    step: int
    action: Action
    state_before: BrowserState | None = None
    state_after: BrowserState | None = None
    error: str | None = None
    timestamp: str = ""


@dataclass
class AgentResult:
    spec_name: str
    goal: str
    passed: bool
    validators: list[ValidatorResult]
    steps: list[StepResult]
    total_steps: int
    duration_ms: int
    error: str | None = None


@dataclass
class Spec:
    goal: str
    validators: list[dict[str, Any]]
    hints: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    buffer: Buffer | None = None
    base_url: str = ""
    timeout_seconds: int = 60
    max_steps: int = 20

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Spec:
        buf = data.get("buffer")
        return cls(
            goal=data["goal"],
            validators=data.get("validators", []),
            hints=data.get("hints", []),
            actions=data.get("actions", []),
            buffer=Buffer(**buf) if buf else None,
            base_url=data.get("base_url", ""),
            timeout_seconds=data.get("timeout_seconds", 60),
            max_steps=data.get("max_steps", 20),
        )
