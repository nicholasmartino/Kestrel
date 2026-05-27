from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

import click
import yaml
from dotenv import load_dotenv

from kestrel.agent import Agent
from kestrel.browser import BrowserManager
from kestrel.llm import LLMClient
from kestrel.ollama_manager import OllamaManager
from kestrel.types import Spec
from kestrel.logging import log_event, print_result

CONFIG_FILE_NAMES = ("kestrel.yml", "kestrel.yaml")

DEFAULT_MODEL = "llama3.2:3b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def _find_config(start: Path) -> dict[str, Any]:
    """Walk up from `start` looking for a kestrel config file."""
    for parent in [start] + list(start.parents):
        for name in CONFIG_FILE_NAMES:
            candidate = parent / name
            if candidate.exists():
                with open(candidate) as f:
                    return dict(yaml.safe_load(f) or {})
    return {}


def _merge_spec_with_config(spec_data: dict[str, Any], config: dict[str, Any]) -> None:
    """Apply config-level defaults to spec data (spec values take precedence)."""
    for key in (
        "action_timeout",
        "wait_action_duration",
        "poll_interval",
        "loop_window",
        "loop_threshold",
    ):
        if key in config and key not in spec_data:
            spec_data[key] = config[key]


def _load_spec(path: Path, config: dict[str, Any] | None = None) -> Spec:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    if config:
        _merge_spec_with_config(data, config)
    return Spec.from_dict(data)


@click.group()
def cli() -> None:
    """Kestrel — Autonomous browser testing agent."""
    pass


@cli.command()
@click.argument("spec_path", type=click.Path(exists=True, path_type=Path))
@click.option("--headless/--headed", default=None, help="Run browser in headless mode")
@click.option("--base-url", default=None, help="Override base URL from spec")
@click.option("--model", default=None, help="Ollama model to use")
@click.option("--ollama-url", default=None, help="Ollama API URL")
@click.option(
    "--browser-args",
    default="",
    help="Additional Chromium launch arguments (space-separated, e.g. --no-sandbox)",
)
@click.option(
    "--env-file",
    type=click.Path(exists=True, path_type=Path),
    help="Load environment variables from file",
)
@click.option(
    "--action-timeout",
    type=float,
    default=None,
    help="Per-action timeout in seconds (default 2.0)",
)
@click.option(
    "--wait-duration",
    type=float,
    default=None,
    help="Duration of a 'wait' action in seconds (default 1.0)",
)
@click.option(
    "--poll-interval",
    type=float,
    default=None,
    help="Polling interval during buffer phase in seconds (default 1.0)",
)
@click.option(
    "--loop-window",
    type=int,
    default=None,
    help="Number of recent steps to inspect for loop detection (default 6)",
)
@click.option(
    "--loop-threshold",
    type=int,
    default=None,
    help="Number of identical hashes within window to trigger loop detection (default 3)",
)
@click.option(
    "--ollama-retry-attempts",
    type=int,
    default=None,
    help="Number of times to retry Ollama health check (default 30)",
)
@click.option(
    "--ollama-retry-interval",
    type=float,
    default=None,
    help="Seconds between Ollama health check retries (default 1.0)",
)
@click.option(
    "--ollama-health-timeout",
    type=float,
    default=None,
    help="Timeout in seconds for Ollama health check request (default 2.0)",
)
def run(
    spec_path: Path,
    headless: bool | None,
    base_url: str | None,
    model: str | None,
    ollama_url: str | None,
    browser_args: str,
    env_file: Path | None,
    action_timeout: float | None,
    wait_duration: float | None,
    poll_interval: float | None,
    loop_window: int | None,
    loop_threshold: int | None,
    ollama_retry_attempts: int | None,
    ollama_retry_interval: float | None,
    ollama_health_timeout: float | None,
) -> None:
    """Run a single spec file."""
    # Load project-level config
    config = _find_config(spec_path.resolve().parent)

    def _from_config(key: str, default: Any) -> Any:
        return config.get(key, default)

    # Resolve CLI args with config file fallback
    resolved_headless = headless if headless is not None else _from_config("headless", True)
    resolved_model = model or _from_config("model", DEFAULT_MODEL)
    resolved_ollama_url = ollama_url or _from_config("ollama_url", DEFAULT_OLLAMA_URL)
    resolved_action_timeout = action_timeout or _from_config("action_timeout", 2.0)
    resolved_wait_duration = wait_duration or _from_config("wait_action_duration", 1.0)
    resolved_poll_interval = poll_interval or _from_config("poll_interval", 1.0)
    resolved_loop_window = loop_window or _from_config("loop_window", 6)
    resolved_loop_threshold = loop_threshold or _from_config("loop_threshold", 3)
    resolved_retry_attempts = ollama_retry_attempts or _from_config("ollama_retry_attempts", 30)
    resolved_retry_interval = ollama_retry_interval or _from_config("ollama_retry_interval", 1.0)
    resolved_health_timeout = ollama_health_timeout or _from_config("ollama_health_timeout", 2.0)

    if env_file:
        load_dotenv(env_file)
    else:
        env_candidate = spec_path.parent / ".env"
        if env_candidate.exists():
            load_dotenv(env_candidate)

    spec = _load_spec(spec_path, config=config)
    if base_url:
        spec.base_url = base_url
    spec.action_timeout = resolved_action_timeout
    spec.wait_action_duration = resolved_wait_duration
    spec.poll_interval = resolved_poll_interval
    spec.loop_window = resolved_loop_window
    spec.loop_threshold = resolved_loop_threshold

    # Substitute env vars in hints, actions, and auth credentials
    def _substitute_env(value: str) -> str:
        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))

        return re.sub(r"[$]\{(\w+)\}", replacer, value)

    spec.hints = [_substitute_env(h) for h in spec.hints]
    spec.actions = [_substitute_env(a) for a in spec.actions]
    if spec.auth:
        spec.auth.credentials = {
            k: _substitute_env(v) for k, v in spec.auth.credentials.items()
        }
        log_event("debug", "Resolved auth credentials", {
            "provider": spec.auth.provider,
            "keys": list(spec.auth.credentials.keys()),
        })
    log_event("debug", "Resolved hints", {"hints": spec.hints})
    log_event("debug", "Resolved actions", {"actions": spec.actions})

    async def _run() -> int:
        ollama = OllamaManager(
            base_url=resolved_ollama_url,
            model=resolved_model,
            retry_attempts=resolved_retry_attempts,
            retry_interval=resolved_retry_interval,
            health_check_timeout=resolved_health_timeout,
        )
        healthy = await ollama.ensure_running()
        if not healthy:
            return 1
        model_ready = await ollama.ensure_model()
        if not model_ready:
            return 1

        parsed_args = browser_args.split() if browser_args else []
        browser = BrowserManager(
            headless=resolved_headless,
            launch_args=parsed_args,
            action_timeout=resolved_action_timeout,
            wait_action_duration=resolved_wait_duration,
        )
        llm = LLMClient(model=resolved_model, base_url=resolved_ollama_url)
        agent = Agent(spec=spec, browser=browser, llm=llm, headless=resolved_headless)
        result = await agent.run()
        print_result(result)
        return 0 if result.passed else 1

    exit_code = asyncio.run(_run())
    sys.exit(exit_code)


@cli.command()
@click.option(
    "--target-repo",
    type=click.Path(path_type=Path),
    default=".",
    help="Path to target repository",
)
def init_workflow(target_repo: Path) -> None:
    """Scaffold autonomous testing into a target repository."""
    repo = target_repo.resolve()
    specs_dir = repo / "tests" / "autonomous" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    example_spec = specs_dir / "login.yml"
    if not example_spec.exists():
        example_spec.write_text(
            'goal: "user can login"\n'
            "validators:\n"
            "  - url_contains: /dashboard\n"
            "  - text_visible: Welcome\n"
            "  - no_console_errors: null\n"
            "hints:\n"
            "  - login page is /login\n"
            "  - use email ${TEST_USER_EMAIL} and password ${TEST_USER_PASSWORD}\n"
            "base_url: http://localhost:5173\n"
            "max_steps: 15\n"
            "timeout_seconds: 60\n"
        )
        click.echo(f"Created example spec: {example_spec}")

    env_example = repo / "tests" / "autonomous" / ".env.example"
    if not env_example.exists():
        env_example.write_text(
            "TEST_USER_EMAIL=your-test-email@example.com\n"
            "TEST_USER_PASSWORD=your-test-password\n"
        )
        click.echo(f"Created example env file: {env_example}")

    workflow_dir = repo / ".github" / "workflows"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    workflow_file = workflow_dir / "autonomous-test.yml"
    if not workflow_file.exists():
        workflow_file.write_text(
            "name: Autonomous Tests\n\n"
            "on:\n"
            "  push:\n"
            "    branches: [main]\n"
            "  pull_request:\n"
            "    branches: [main]\n\n"
            "jobs:\n"
            "  autonomous:\n"
            "    runs-on: self-hosted\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n\n"
            "      - name: Set up Python\n"
            "        uses: actions/setup-python@v5\n"
            "        with:\n"
            '          python-version: "3.11"\n\n'
            "      - name: Install Kestrel\n"
            "        run: pip install kestrel\n\n"
            "      - name: Install Playwright browsers\n"
            "        run: playwright install chromium\n\n"
            "      - name: Run autonomous tests\n"
            "        env:\n"
            "          TEST_USER_EMAIL: ${{ secrets.TEST_USER_EMAIL }}\n"
            "          TEST_USER_PASSWORD: ${{ secrets.TEST_USER_PASSWORD }}\n"
            "        run: kestrel run tests/autonomous/specs/login.yml\n\n"
            "      - name: Upload results\n"
            "        if: always()\n"
            "        uses: actions/upload-artifact@v4\n"
            "        with:\n"
            "          name: kestrel-results\n"
            "          path: kestrel-results.json\n"
        )
        click.echo(f"Created workflow: {workflow_file}")
    else:
        click.echo(f"Workflow already exists: {workflow_file}")

    click.echo("Done. Review the generated files and commit them.")


def main() -> None:
    cli()
