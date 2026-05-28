# Spec-Driven Testing with Kestrel: Best Practices

A guide to writing robust, reliable spec files for Kestrel's LLM-driven E2E testing.

---

## Table of Contents

1. [Spec Anatomy](#1-spec-anatomy)
2. [Target Selection](#2-target-selection)
3. [Action Patterns](#3-action-patterns)
4. [Goals & Hints](#4-goals--hints)
5. [Pre-seeded Data & DB State](#5-pre-seeded-data--db-state)
6. [Authentication](#6-authentication)
7. [YAML Pitfalls](#7-yaml-pitfalls)
8. [Validators & Teardown](#8-validators--teardown)
9. [Timeouts & Limits](#9-timeouts--limits)
10. [Debugging Failed Specs](#10-debugging-failed-specs)

---

## 1. Spec Anatomy

A spec file is a YAML document with these fields:

```yaml
goal: "A clear, single-sentence description of what the test does"
base_url: http://localhost:5173/page
max_steps: 8
timeout_seconds: 180
action_timeout: 15
auth:
    provider: clerk
    credentials:
        secret_key: ${CLERK_SECRET_KEY}
        identifier: ${E2E_USER_EMAIL}
        password: ${E2E_USER_PASSWORD}
hints:
  - constraints and guidance for the LLM (not action steps)
actions:
  - click Add
  - click Save
  - type 5000 into Balance
teardown:
  - action: click
    target: $ 5.0K
  - action: wait
  - action: type
    target: Balance
    text: "0"
validators:
  - text_visible: "5.0K"
  - no_console_errors: null
```

| Field | Required | Purpose |
|---|---|---|
| `goal` | Yes | Describes the test in natural language; the LLM uses this to decide what to do |
| `base_url` | Yes | Starting page URL |
| `auth` | No | Auth provider configuration (e.g., Clerk) |
| `actions` | No | Ordered list of action descriptions the LLM should follow |
| `hints` | No | Constraints and guidance (not action steps) |
| `validators` | No | Post-action checks (text visibility, console errors) |
| `teardown` | No | Structured actions to reset state after the spec runs |
| `max_steps` | No | Max LLM action generations (default varies) |
| `timeout_seconds` | No | Total spec timeout |
| `action_timeout` | No | Per-action timeout in seconds (default 2 — usually too low) |

---

## 2. Target Selection

Target selection is the single most important skill for reliable specs. A bad target
causes the LLM to click the wrong element, or the LLM generates a click when you
need a type, or the action times out waiting for an element that never appears.

### 2.1 The Shortest Unique Substring Rule

**Use the shortest substring that uniquely identifies the target element.**

This works for both click and type targets because Kestrel uses `exact=False`
(substring matching) throughout its fallback chains.

```yaml
# GOOD — "Add" matches both "Add Account" (empty-state button text)
# and "Add new account" (toolbar Plus icon accessible name via title attribute)
actions:
  - click Add

# BAD — only matches the empty-state text; breaks when toolbar icon is shown
actions:
  - click Add Account

# BAD — only matches the toolbar accessible name; breaks on empty state
actions:
  - click Add new account
```

**Why this matters:** Clerk authentication tokens are deterministic per app — all
tokens resolve to the same test user. The database accumulates state across CI runs.
On the first run, the page might show an empty state with a button labeled
"Add Account". On later runs, that account already exists, so the empty state
disappears and a toolbar Plus icon (with `title="Add new account"`) is shown instead.
`get_by_role("button", name="Add")` matches both because the accessible name in
both cases contains "Add".

### 2.2 Avoid Column Headers as Click Targets

Kestrel's click action tries `get_by_text(target, exact=False).first` as its
first strategy. Column headers are `<th>` elements with visible text, so a target
matching a column name will hit the header, not the data cell.

```yaml
# BAD — "Balance" matches the <th>Balance</th> column header first
# Clicking the header sorts the table, does NOT enter edit mode
actions:
  - click Balance

# GOOD — "$ 0" matches the data cell's text content uniquely
# Kestrel's get_by_text("$ 0") finds the cell, clicking it enters edit mode
actions:
  - click $ 0
```

**Rule of thumb:** Use display values (the formatted number shown in the cell)
as click targets. Use `aria-label` values as type targets.

### 2.3 What Works for Click Targets

| Target | Matches via | Example |
|---|---|---|
| Visible text in the element | `get_by_text()` | `$ 0`, `Add`, `Save`, `5.0K` |
| `aria-label` attribute | `get_by_label()` | `Allocation info` (on an icon) |
| `title` attribute accessible name | `get_by_role("button", name=...)` | `Add` (matches `title="Add new account"`) |
| `role="button"` + text | `get_by_role("button", name=...)` | After adding `role="button"` and `aria-label` |

Kestrel's click fallback chain (simplified):

```
get_by_text(target, exact=False).first.click()
  → get_by_label(target, exact=False).first.click()
    → get_by_role("button", name=target, exact=False).first.click()
      → CSS/force-click/JS shadow-DOM fallbacks
```

### 2.4 What Works for Type Targets

**The type target must match the input element's `aria-label`** (or placeholder,
name attribute, etc. via fallbacks).

```yaml
# GOOD — the <input> has aria-label="Balance"
actions:
  - type 5000 into Balance

# BAD — the column header text is "Balance", not the input's label
actions:
  - type 5000 into balance column
```

If the input has no `aria-label`, the type action searches by placeholder, `name`
attribute, CSS selector, and finally a JS shadow-DOM fallback. **Always add an
`aria-label` to inline-editable fields** that will be targeted by type actions.

Kestrel's type fallback chain (simplified):

```
get_by_label(label, exact=False).first.fill(text)
  → get_by_placeholder(label, exact=False).first.fill(text)
    → input[name='name'].first.fill(text)
      → #id .first.fill(text)
        → JS shadow-DOM fallback
```

### 2.5 The Click-to-Reveal Pattern

Many inline-editing UIs use a two-phase interaction:
1. Click a display div (shows formatted value, e.g., "$ 0")
2. The div is replaced by an `<input>` with the same `aria-label`
3. Type the new value into the revealed input

**Problem:** The LLM frequently skips step 1 (the click) and tries to type
directly. The input isn't visible yet, so the type action fails.

**Two solutions (use both):**

1. **Add `aria-label` to the display div** — so the type action can find it even
   before clicking. Kestrel's type action now falls through to click the display
   div when `fill()` fails, then retries filling the revealed input.

2. **Always include both a click and type action** in the spec:
   ```yaml
   actions:
     - click $ 0       # enters edit mode
     - type 5000 into Balance  # fills the revealed input
   ```

### 2.6 CSS Selectors Don't Work

The LLM frequently truncates or misparses CSS selector syntax in targets.

```yaml
# BAD — the LLM generates click [aria-label=" instead of click [aria-label="Qty"]
actions:
  - click [aria-label="Qty"]

# GOOD — plain text targets work reliably
actions:
  - click Qty
```

### 2.7 Examples

**Account editing:**
```yaml
# The balance cell displays "$ 0" and has aria-label="Balance"
actions:
  - click $ 0            # clicks the display cell
  - type 5000 into Balance  # fills the revealed input
```

**Deposit yield editing:**
```yaml
# The yield cell displays "—" (em dash for zero) and has aria-label="Yield"
actions:
  - click Yield          # matches aria-label="Yield" on display div
  - type 5.25 into Yield # fills the revealed input
```

**Security count editing:**
```yaml
# The count cell displays "0" and has aria-label="Qty"
actions:
  - click Qty            # matches aria-label="Qty" on display div
  - type 25 into Qty     # fills the revealed input
```

**Opening a popover then editing:**
```yaml
# The info icon has aria-label="Allocation info"
actions:
  - click Allocation info  # opens the popover
  - wait briefly           # wait for popover animation
  - type 25 into Qty       # edits count inside the popover
```

---

## 3. Action Patterns

### 3.1 Split Click + Type

Always separate click and type actions. The LLM does not reliably infer that a
type action requires a preceding click.

```yaml
# GOOD — explicit two-step sequence
actions:
  - click $ 0
  - type 5000 into Balance

# BAD — the LLM generates click Balance, skipping the type
actions:
  - type 5000 into Balance
```

### 3.2 Keep Lists Short

LLMs tend to follow the first 3-4 actions faithfully and drop or merge later ones.

```yaml
# GOOD — 3-4 actions, each achieving one step
actions:
  - click Add
  - click Save
  - click $ 0
  - type 5000 into Balance

# BAD — the LLM gets confused by the long list and skips steps
actions:
  - click the Accounts button to go to the accounts page
  - click Add Account to create a new inline row
  - click the Save button
  - wait for the new account row to appear
  - switch to the Securities tab
  - click Add to create a new security row
  - ... (10+ more)
```

### 3.3 First Action Sets the Pattern

The LLM tends to generate the same action type as its first response. If the
first action in your spec list is `click`, the LLM may generate `click` for
every subsequent step, even when it should `type`.

```yaml
# GOOD — type action is present and LLM generates it
actions:
  - click $ 0
  - type 5000 into Balance

# If step 2 is missing, the LLM will click again instead of typing
actions:
  - click $ 0
  # missing: - type 5000 into Balance
```

### 3.4 Add "wait" After Overlays

When opening a modal, popover, or dynamically rendered section, add a `wait
briefly` action to give the UI time to render before the next action targets
elements inside it.

```yaml
actions:
  - click Allocation info  # opens a popover
  - wait briefly           # popover needs ~300ms to animate in
  - type 25 into Qty       # targets element inside popover
```

### 3.5 Examples

**Simple CRUD:**
```yaml
actions:
  - click Add        # create new row
  - click Save       # save with defaults
  - click $ 0        # edit the balance
  - type 5000 into Balance  # set new value
```

**Teardown (structured, not LLM-generated):**
```yaml
teardown:
  - action: click
    target: $ 5.0K
  - action: wait
  - action: type
    target: Balance
    text: "0"
```

---

## 4. Goals & Hints

### 4.1 Goal Text Must Use Raw Input Values

The LLM reads the goal and often types the exact value it sees there — including
formatting. If the goal says `$5.0K` or `5,000`, the LLM types those strings
instead of the raw number.

```yaml
# GOOD — goal uses raw input value
goal: "Create an account and edit its balance from 0 to 5000"

# BAD — goal uses formatted display value
# The LLM types "5.0K" instead of "5000"
goal: "Create an account and edit its balance from 0 to $5.0K"

# BAD — goal uses locale-formatted value
# The LLM types "5,000" which the input may not accept
goal: "Create an account and edit its balance from 0 to 5,000"
```

### 4.2 Hints for Constraints Only

Hints are most effective for things the LLM can't infer from the action list
or page state. Use them for constraints, not step-by-step instructions.

```yaml
hints:
  # GOOD — prevents the LLM from navigating away
  - do NOT goto or navigate — use click, type, and wait only

  # GOOD — prevents the LLM from using the chat widget
  - do NOT use the chat input — ignore it completely

  # GOOD — explains interaction model the LLM might not understand
  - click the value to open the editor, then type the new value

  # BAD — this belongs in the actions list, not hints
  - click Add, then click Save, then click $ 0, then type 5000
```

### 4.3 Examples

```yaml
goal: "Create an account and edit its balance from 0 to 5000, verify the optimistic update"
hints:
  - do NOT goto or navigate — use click, type, and wait only
  - do NOT use the chat input — ignore it completely
```

```yaml
goal: "Update the security count from 0 to 25 on the securities page"
hints:
  - do NOT goto or navigate — use click, type, and wait only
  - type 25 into Qty to update the security count
```

---

## 5. Pre-seeded Data & DB State

### 5.1 Clerk Tokens Are Deterministic

All Clerk testing tokens for the same Clerk application resolve to the **same
test user**. This means:

- Every CI run uses the same `user_id`
- The database accumulates state across runs — accounts, deposits, and
  securities created in one run persist for the next
- Empty-state UI elements disappear once they've been triggered

### 5.2 Pre-seed Data Instead of Creating In-Spec

Creation flows (searching for a ticker symbol, filling multi-field forms) are
fragile with LLM-driven testing. The search API might be down, the autocomplete
might not return results, or the LLM might skip required fields.

```yaml
# GOOD — pre-seed the account and security in the database,
# then the spec just edits an existing value
actions:
  - click Qty
  - type 25 into Qty

# BAD — creating from scratch requires search API, multi-step forms
actions:
  - click Add
  - search for AAPL in the ticker search field
  - select the first result
  - pick an account from the dropdown
  - click Save
  - edit the count
```

### 5.3 Teardown Is Mandatory

Without a teardown, a successful run leaves the database in a modified state.
The next run starts with stale data, and the spec becomes a no-op (the
validator passes because the expected value is already visible).

```yaml
# GOOD — resets the balance to 0 after the test
teardown:
  - action: click
    target: $ 5.0K
  - action: wait
  - action: type
    target: Balance
    text: "0"

# BAD — no teardown; next run sees "5.0K" already visible
# The spec "passes" without actually doing anything
```

### 5.4 Examples

**Pre-seeded account + balance update:**
```yaml
goal: "Create an account and change its balance to 5000"
base_url: http://localhost:5173/accounts
action_timeout: 15
actions:
  - click Add
  - click Save
  - click $ 0
  - type 5000 into Balance
teardown:
  - action: click
    target: $ 5.0K
  - action: wait
  - action: type
    target: Balance
    text: "0"
validators:
  - text_visible: "5.0K"
  - no_console_errors: null
```

---

## 6. Authentication

### 6.1 Provider Configuration

```yaml
auth:
    provider: clerk
    credentials:
        secret_key: ${CLERK_SECRET_KEY}
        identifier: ${E2E_USER_EMAIL}
        password: ${E2E_USER_PASSWORD}
```

The `secret_key` is the Clerk API key used to generate a testing token.
The `identifier` and `password` are the test user's credentials for the
Clerk-hosted sign-in form.

### 6.2 Token Lifecycle

1. Kestrel calls `POST /v1/testing_tokens` with the `secret_key`
2. Clerk returns a token that resolves to a specific test user
3. The token is valid for the duration of the spec
4. **All tokens for the same Clerk app resolve to the same test user**

### 6.3 State Accumulation Pattern

Because the user is the same across runs:

| Run | State | What the LLM sees |
|---|---|---|
| 1st | Empty DB | Empty-state "Add Account" button |
| 2nd | Account exists | Toolbar Plus icon with `title="Add new account"` |
| 3rd | Balance was set to 5000 | Cell shows "$5.0K" instead of "$0" |

The "Add" target handles both states (see [Section 2.1](#21-the-shortest-unique-substring-rule)).
The teardown handles state reset (see [Section 5.3](#53-teardown-is-mandatory)).

---

## 7. YAML Pitfalls

### 7.1 Colons in List Items

YAML interprets a colon followed by a space inside an unquoted list item as a
key-value separator, converting the string into a dict. Kestrel's
`_substitute_env` function crashes with `TypeError: expected string or
bytes-like object, got 'dict'` when this happens.

```yaml
# BAD — YAML parses "action_timeout: 15" as a dict
actions:
  - click Add
  - wait action_timeout: 15
  - click Save

# GOOD — no colon, or quote the string
actions:
  - click Add
  - wait briefly
  - click Save

# ALSO GOOD — quoted
actions:
  - click Add
  - "wait action_timeout: 15"
  - click Save
```

**Best practice:** Never use colons inside YAML list item strings. Use
commas or rephrase to avoid the colon.

---

## 8. Validators & Teardown

### 8.1 Always Include `no_console_errors`

This validator catches JavaScript errors that might not otherwise cause a spec
failure (e.g., network failures, React rendering errors).

```yaml
validators:
  - text_visible: "5.0K"     # verify the optimistic update
  - no_console_errors: null   # catch hidden JS errors
```

### 8.2 Teardown Uses Structured Actions

Unlike `actions` (which are natural language strings for the LLM), `teardown`
uses structured action objects that Kestrel executes directly:

```yaml
teardown:
  - action: click
    target: $ 5.0K
  - action: wait
  - action: type
    target: Balance
    text: "0"
```

Supported teardown actions:
- `click` — clicks an element by target
- `type` — fills an input with text
- `wait` — pauses briefly (no target needed)

### 8.3 Validator Failure vs Action Failure

- **Action failure** — the element wasn't found; the LLM gets another chance
  (up to `max_steps`)
- **Validator failure** — the action succeeded but the expected state wasn't
  reached; the LLM also gets another chance

---

## 9. Timeouts & Limits

### 9.1 Always Set `action_timeout: 15`

The default per-action timeout (2 seconds) is too short for pages that load
data from an API. Pre-seeded accounts and securities need time to load.

```yaml
# GOOD — 15 seconds per action gives API calls time to resolve
action_timeout: 15

# BAD — 2 seconds is not enough for async data loading
# (this is the default; always override it)
```

### 9.2 Set `max_steps` Generously

A simple edit workflow needs 3-4 actions. Allow 8 to give the LLM room for
an occasional mis-step. Avoid 20+ step specs — the LLM runs out of token
budget and starts hallucinating.

```yaml
# GOOD — simple edit workflow
max_steps: 8

# BAD — too many steps; LLM loses context
max_steps: 30
```

### 9.3 Set `timeout_seconds: 180`

3 minutes is usually enough for simple specs. Longer specs (with multiple
navigations or API-heavy workflows) may need 300+.

---

## 10. Debugging Failed Specs

### 10.1 Read the LLM Response Trace

Kestrel logs every LLM-generated action as a JSON debug message:

```
LLM response: {"action": "click", "target": "$ 0"}
```

This tells you exactly what the LLM decided to do. If the LLM generates
`click Balance` when you expected `type 5000 into Balance`, the target is
wrong, or the type action isn't clear enough.

### 10.2 Distinguish Error Types

The spec result JSON includes per-step errors:

```json
{
  "total_steps": 3,
  "error": "Validators failed",
  "steps": [
    { "step": 0, "error": null },                          // auth/nav OK
    { "step": 1, "error": null },                          // action succeeded
    { "step": 2, "error": "Could not find clickable element for: Save" }  // action failed
  ]
}
```

- **`null` error** — the action executed successfully
- **`Could not find clickable element`** — the target wasn't found in the DOM.
  Check if the element exists, is visible, and the target substring matches.
- **`Could not find input element`** — same, but for type actions. Check the
  input's `aria-label`.
- **`Validators failed`** — the action(s) succeeded but the expected state
  wasn't reached. Check the validator conditions.

### 10.3 Check the Resolved Hints

Kestrel logs the resolved hints after environment variable substitution:

```
Resolved hints: ["do NOT goto or navigate", "click Add to start a new account row"]
```

If the hints contain unsubstituted variables (e.g., `${UNDEFINED_VAR}`) or
were parsed incorrectly, the LLM sees garbled instructions.

### 10.4 Common Failure Patterns

| Symptom | Likely Cause | Fix |
|---|---|---|
| LLM generates `click` when spec says `type` | Target matches visible text; LLM prefers clicking | Add the action text in the actions list |
| LLM generates same action twice | Validator didn't pass; LLM doesn't know what else to do | Add more actions to give the LLM options |
| Element not found on first run only | Clerk token mismatch (pre-seeded data is for wrong user) | Pre-seed by the correct Clerk test user |
| Element not found on subsequent runs | DB state accumulated; empty-state elements hidden | Use a target that works for both states ("Add") |
| Colons in YAML cause parse errors | YAML treats `:` as dict separator | Remove colons or quote the entire string |
| LLM types formatted value from goal | Goal contains `$5.0K` or `5,000` instead of `5000` | Use raw values in the goal |
| Type action fails but click on same target works | Input has no `aria-label` matching the target | Add `aria-label` to the input element |
