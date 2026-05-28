# Kestrel

Autonomous browser-testing agent powered by Playwright and local LLMs (Ollama).

Kestrel operates a live browser session dynamically. It does **not** generate test files. Instead, it uses an LLM as a decision engine to navigate and interact with web applications, while deterministic validators serve as the sole source of truth for pass/fail.

## Architecture

- **Playwright** is the execution engine
- **LLM** (Llama 3.2 via Ollama) is the decision/planning engine
- **Validators** determine pass/fail deterministically
- **Agent** explores and recovers autonomously

## Prerequisites

- Python 3.10+
- [Ollama](https://ollama.com/) installed
- Git

## Installation

```bash
pip install git+https://github.com/nicholasmartino/kestrel.git
```

Or install locally for development:

```bash
git clone https://github.com/nicholasmartino/kestrel.git
cd kestrel
pip install -e .
playwright install chromium
```

## Quick Start

### 1. Start Ollama

```bash
ollama serve
```

### 2. Pull the model

```bash
ollama pull llama3.2:3b
```

### 3. Write a spec

Create `specs/login.yml`:

```yaml
goal: "user can login"
validators:
  - url_contains: /dashboard
  - text_visible: Welcome
  - no_console_errors: null
hints:
  - login page is /login
  - use email ${TEST_USER_EMAIL} and password ${TEST_USER_PASSWORD}
base_url: http://localhost:5173
max_steps: 15
timeout_seconds: 60
```

### 4. Run

```bash
kestrel run specs/login.yml
```

Or with a local env file:

```bash
kestrel run specs/login.yml --env-file specs/.env
```

## CLI

```
Usage: kestrel [OPTIONS] COMMAND [ARGS]...

Commands:
  run            Run a single spec file
  init-workflow  Scaffold autonomous testing into a target repository
```

### `kestrel run`

```bash
kestrel run specs/login.yml \
  --headless \
  --base-url http://localhost:5173 \
  --model llama3.2:3b \
  --ollama-url http://localhost:11434
```

### `kestrel init-workflow`

Scaffolds CI workflow and example specs into a target repo:

```bash
cd /path/to/your-web-app
kestrel init-workflow --target-repo .
```

This creates:

- `tests/autonomous/specs/login.yml`
- `tests/autonomous/.env.example`
- `.github/workflows/autonomous-test.yml`

## Best Practices

See [docs/best-practices.md](docs/best-practices.md) for a comprehensive guide on
writing reliable specs, including target selection, action patterns, handling
pre-seeded data, and debugging failures.

## Spec Format

Specs are YAML files with the following structure:

| Field             | Type   | Description                                    |
| ----------------- | ------ | ---------------------------------------------- |
| `goal`            | string | High-level objective for the LLM               |
| `validators`      | list   | Deterministic assertions (see below)           |
| `hints`           | list   | Optional guidance for the LLM                  |
| `base_url`        | string | Starting URL                                   |
| `max_steps`       | int    | Maximum actions before giving up (default: 20) |
| `timeout_seconds` | int    | Global timeout (default: 60)                   |

## Validators

Validators are the **only** source of truth for pass/fail.

| Validator           | Example                                  | Description                                    |
| ------------------- | ---------------------------------------- | ---------------------------------------------- |
| `url_contains`      | `url_contains: /dashboard`               | Passes if current URL contains substring       |
| `text_visible`      | `text_visible: Welcome`                  | Passes if text appears anywhere on page        |
| `no_console_errors` | `no_console_errors: null`                | Passes if no console errors occurred           |
| `network_status`    | `network_status: "POST /checkout = 200"` | Passes if matching request has expected status |

## Extending Validators

Register custom validators in your own module:

```python
from kestrel.validators import register
from kestrel.types import BrowserState, ValidatorResult

@register("custom_check")
def my_validator(state: BrowserState, arg):
    return ValidatorResult(
        name="custom_check",
        passed=True,
        detail="All good",
    )
```

## CI / GitHub Actions

Use `kestrel init-workflow` to generate a workflow for self-hosted runners.

Required secrets:

- `TEST_USER_EMAIL`
- `TEST_USER_PASSWORD`

The workflow assumes:

- Self-hosted runner with Python 3.11+
- Ollama installed (Kestrel will auto-start if needed)
- Network access to Ollama on `localhost:11434`

## State Extraction

Kestrel sends a **compressed state** to the LLM:

```json
{
  "url": "...",
  "title": "...",
  "visible_text": ["..."],
  "buttons": ["..."],
  "inputs": ["..."],
  "links": ["..."],
  "console_errors": ["..."],
  "network_failures": ["..."]
}
```

Full HTML and screenshots are **never** sent unless explicitly needed.

## Action Schema

The LLM must return **only** JSON:

```json
{"action": "goto", "url": "https://example.com"}
{"action": "click", "target": "Submit"}
{"action": "type", "target": "Email", "text": "user@example.com"}
{"action": "wait"}
{"action": "done"}
```

## Agent Loop Features

- **Retry handling**: Failed actions are reported back to the LLM
- **Loop detection**: Repeating the same state/action pattern 3+ times triggers failure
- **Max step count**: Configurable limit (default 20)
- **Timeout handling**: Global timeout per spec (default 60s)
- **Action history**: Last 10 actions included in prompts
- **Structured logs**: JSON logs to stderr; results to stdout

## License

MIT
