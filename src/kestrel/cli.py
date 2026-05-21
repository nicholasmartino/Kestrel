from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import click
import yaml
from dotenv import load_dotenv

from kestrel.agent import Agent
from kestrel.browser import BrowserManager
from kestrel.llm import LLMClient
from kestrel.ollama_manager import OllamaManager
from kestrel.types import Spec
from kestrel.logging import log_event, print_result


def _load_spec(path: Path) -> Spec:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return Spec.from_dict(data)


@click.group()
def cli() -> None:
    """Kestrel — Autonomous browser testing agent."""
    pass


@cli.command()
@click.argument("spec_path", type=click.Path(exists=True, path_type=Path))
@click.option("--headless/--headed", default=True, help="Run browser in headless mode")
@click.option("--base-url", default="", help="Override base URL from spec")
@click.option("--model", default="llama3.2:3b", help="Ollama model to use")
@click.option("--ollama-url", default="http://localhost:11434", help="Ollama API URL")
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
def run(
    spec_path: Path,
    headless: bool,
    base_url: str,
    model: str,
    ollama_url: str,
    browser_args: str,
    env_file: Path | None,
) -> None:
    """Run a single spec file."""
    if env_file:
        load_dotenv(env_file)
    else:
        # Try to load .env from the directory containing the spec
        env_candidate = spec_path.parent / ".env"
        if env_candidate.exists():
            load_dotenv(env_candidate)

    spec = _load_spec(spec_path)
    if base_url:
        spec.base_url = base_url

    # Substitute env vars in hints (e.g. ${TEST_USER_EMAIL})
    def _substitute_env(value: str) -> str:
        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, match.group(0))

        return re.sub(r"\$\{(\w+)\}", replacer, value)

    spec.hints = [_substitute_env(h) for h in spec.hints]

    async def _run() -> int:
        # Ensure Ollama
        ollama = OllamaManager(base_url=ollama_url, model=model)
        healthy = await ollama.ensure_running()
        if not healthy:
            return 1
        model_ready = await ollama.ensure_model()
        if not model_ready:
            return 1

        # Run agent
        parsed_args = browser_args.split() if browser_args else []
        browser = BrowserManager(headless=headless, launch_args=parsed_args)
        llm = LLMClient(model=model, base_url=ollama_url)
        agent = Agent(spec=spec, browser=browser, llm=llm, headless=headless)
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
            "        run: pip install git+https://github.com/nicholasmartino/kestrel.git@main\n\n"
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
