from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from kestrel.types import BrowserState, Action
from kestrel.logging import log_event


class BrowserManager:
    def __init__(self, headless: bool = True, launch_args: list[str] | None = None):
        self.headless = headless
        base_args = launch_args or []
        extra_args = [
            "--winhttp-proxy-resolver",
            "--disable-features=NetworkService,NetworkServiceInProcess",
        ]
        # Merge, deduplicating
        self._launch_args = base_args + [a for a in extra_args if a not in base_args]
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._console_logs: list[str] = []
        self._network_logs: list[str] = []
        self._network_requests: list[str] = []

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        log_event("debug", "Launching Chromium", {"args": self._launch_args})
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=self._launch_args,
        )
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()

        self._page.on("console", self._on_console)
        self._page.on("requestfinished", self._on_request)

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    @property
    def page(self) -> Page | None:
        return self._page

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    def _on_console(self, msg: Any) -> None:
        if msg.type == "error":
            self._console_logs.append(str(msg.text))

    async def _on_request(self, request: Any) -> None:
        try:
            response = await request.response()
            if response:
                status = response.status
                entry = f"{request.method} {request.url} {status}"
                self._network_requests.append(entry)
                if status >= 400:
                    self._network_logs.append(entry)
        except Exception:
            pass

    async def extract_state(self) -> BrowserState:
        if self._page is None:
            raise RuntimeError("Browser not started")

        page = self._page
        url = page.url
        title = await page.title()

        # Use accessibility snapshot when available
        try:
            snapshot = await page.accessibility.snapshot()
            accessibility_tree = self._serialize_snapshot(snapshot)
        except Exception:
            accessibility_tree = ""

        # Extract visible interactive elements via page.evaluate
        elements = await page.evaluate("""
            () => {
                const results = { buttons: [], inputs: [], filled_inputs: [], links: [], visible_text: [] };

                function qsaDeep(selector, root = document) {
                    const found = Array.from(root.querySelectorAll(selector));
                    root.querySelectorAll('*').forEach(el => {
                        if (el.shadowRoot)
                            found.push(...qsaDeep(selector, el.shadowRoot));
                    });
                    return found;
                }

                qsaDeep('button, [role="button"]').forEach(el => {
                    const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
                    if (text) results.buttons.push(text);
                });
                const inputEntries = [];
                qsaDeep('input, textarea, select, [contenteditable="true"]').forEach(el => {
                    const label = el.labels?.[0]?.innerText.trim() ?? '';
                    const name = el.getAttribute('name') || el.getAttribute('placeholder') || el.getAttribute('aria-label') || '';
                    const isEmpty = (el.value ?? '').trim() === '';
                    let entry;
                    if (label && name) {
                        entry = `${name} (${label})`;
                    } else if (name) {
                        entry = name;
                    } else if (label) {
                        entry = label;
                    } else {
                        entry = 'input';
                    }
                    results.inputs.push(entry);
                    if (!isEmpty) results.filled_inputs.push(entry);
                });
                qsaDeep('a').forEach(el => {
                    const text = (el.innerText || el.getAttribute('aria-label') || '').trim();
                    if (text) results.links.push(text);
                });
                qsaDeep('*').forEach(el => {
                    if (el.childNodes) {
                        el.childNodes.forEach(child => {
                            if (child.nodeType === 3) {
                                const text = (child.textContent || '').trim();
                                if (text && text.length < 200) results.visible_text.push(text);
                            }
                        });
                    }
                    if (el.children.length === 0 && el.parentNode) {
                        const text = (el.innerText || '').trim();
                        if (text && text.length < 200) results.visible_text.push(text);
                    }
                });
                return results;
            }
        """)

        state = BrowserState(
            url=url,
            title=title,
            visible_text=list(set(elements.get("visible_text", []))),
            buttons=list(set(elements.get("buttons", []))),
            inputs=list(set(elements.get("inputs", []))),
            filled_inputs=list(set(elements.get("filled_inputs", []))),
            links=list(set(elements.get("links", []))),
            console_errors=list(self._console_logs),
            network_failures=list(self._network_logs),
            network_requests=list(self._network_requests),
            accessibility_tree=accessibility_tree,
        )

        # Clear ephemeral logs after capture
        self._console_logs.clear()
        self._network_logs.clear()
        self._network_requests.clear()

        return state

    def _serialize_snapshot(
        self, snapshot: dict[str, Any] | None, depth: int = 0
    ) -> str:
        if not snapshot:
            return ""
        lines: list[str] = []
        role = snapshot.get("role", "")
        name = snapshot.get("name", "")
        if role and name:
            lines.append(f"{'  ' * depth}{role}: {name}")
        for child in snapshot.get("children", []):
            lines.append(self._serialize_snapshot(child, depth + 1))
        return "\n".join(lines)

    async def execute(self, action: Action) -> str | None:
        """Execute a single action safely. Returns error message or None."""
        if self._page is None:
            return "Browser not started"

        page = self._page
        try:
            if action.action == "goto":
                if action.url:
                    await page.goto(action.url, wait_until="domcontentloaded")
                else:
                    return "goto missing url"

            elif action.action == "click":
                if action.target:
                    await self._click_by_text_or_label(action.target)
                else:
                    return "click missing target"

            elif action.action == "type":
                if action.target and action.text is not None:
                    await self._type_by_text_or_label(action.target, action.text)
                else:
                    return "type missing target or text"

            elif action.action == "wait":
                await asyncio.sleep(1)

            elif action.action == "done":
                pass

        except Exception as exc:
            return str(exc)

        return None

    async def _click_by_text_or_label(self, target: str) -> None:
        """Attempt to click by accessible text, label, or selector fallback."""
        page = self._page
        if page is None:
            return

        # Try text match first
        try:
            await page.get_by_text(target, exact=False).first.click(timeout=2000)
            return
        except Exception:
            pass

        # Try label match
        try:
            await page.get_by_label(target, exact=False).first.click(timeout=2000)
            return
        except Exception:
            pass

        # Try button role+name
        try:
            await page.get_by_role("button", name=target, exact=False).first.click(
                timeout=2000
            )
            return
        except Exception:
            pass

        # Try CSS selector without force
        try:
            if target.startswith("#") or target.startswith(".") or target.startswith("["):
                await page.locator(target).first.click(timeout=2000)
                return
        except Exception:
            pass

        # Force-click fallback: bypass overlays (cookie consent, modals, etc.)
        try:
            await page.get_by_text(target, exact=False).first.click(
                timeout=2000, force=True
            )
            return
        except Exception:
            pass

        try:
            await page.get_by_role("button", name=target, exact=False).first.click(
                timeout=2000, force=True
            )
            return
        except Exception:
            pass

        # Force click with case-insensitive CSS matching
        try:
            css_target = re.sub(r'\[(\w+)=([^\]]+)\]', r'[\1=\2 i]', target)
            await page.locator(css_target).first.click(timeout=2000, force=True)
            return
        except Exception:
            pass

        # JS shadow-DOM-piercing fallback
        try:
            clicked = await page.evaluate(
                """
                (args) => {
                    function qsa(sel, root) {
                        const found = Array.from(root.querySelectorAll(sel));
                        root.querySelectorAll('*').forEach(el => {
                            if (el.shadowRoot) found.push(...qsa(sel, el.shadowRoot));
                        });
                        return found;
                    }
                    const target = args.target.toLowerCase();
                    const candidates = qsa('button, [role="button"], a, [onclick]', document);
                    for (const el of candidates) {
                        const text = (el.innerText || el.getAttribute('aria-label') || '').trim().toLowerCase();
                        if (text && text.includes(target)) {
                            el.focus();
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
                """,
                {"target": target},
            )
            if clicked:
                return
        except Exception:
            pass

        raise RuntimeError(f"Could not find clickable element for: {target}")

    async def _type_by_text_or_label(self, target: str, text: str) -> None:
        """Attempt to type into an input by label, placeholder, or selector."""
        page = self._page
        if page is None:
            return

        # Extract label from "name (label)" format used in state extraction
        label_match = re.match(r"^(.+?)\s*\((.+)\)$", target)
        label = label_match.group(2) if label_match else target
        name = label_match.group(1) if label_match else target

        # Try the extracted label first
        try:
            await page.get_by_label(label, exact=False).first.fill(text, timeout=2000)
            return
        except Exception:
            pass

        # Try the raw target as label
        if label != target:
            try:
                await page.get_by_label(target, exact=False).first.fill(text, timeout=2000)
                return
            except Exception:
                pass

        try:
            await page.get_by_placeholder(label, exact=False).first.fill(
                text, timeout=2000
            )
            return
        except Exception:
            pass

        # Try input[name='name'] with the extracted name
        try:
            await page.locator(f"input[name='{name}']").first.fill(text, timeout=2000)
            return
        except Exception:
            pass

        # Case-insensitive CSS name match
        try:
            await page.locator(f"input[name='{name}' i]").first.fill(text, timeout=2000)
            return
        except Exception:
            pass

        # Try matching by id (common pattern with htmlFor labels)
        try:
            await page.locator(f"#{name.lower()}").first.fill(text, timeout=2000)
            return
        except Exception:
            pass

        # Force fill as last resort to bypass overlays (cookie consent, etc.)
        try:
            await page.locator(f"input[name='{name}' i]").first.fill(text, timeout=2000, force=True)
            return
        except Exception:
            pass

        # Fallback to CSS selector
        if target.startswith("#") or target.startswith(".") or target.startswith("["):
            try:
                await page.locator(target).first.fill(text, timeout=2000, force=True)
                return
            except Exception:
                pass

        # JS shadow-DOM-piercing fallback (mirrors extract_state)
        try:
            filled = await page.evaluate(
                """
                (args) => {
                    function qsa(sel, root) {
                        const found = Array.from(root.querySelectorAll(sel));
                        root.querySelectorAll('*').forEach(el => {
                            if (el.shadowRoot) found.push(...qsa(sel, el.shadowRoot));
                        });
                        return found;
                    }
                    const target = args.target.toLowerCase();
                    const text = args.text;
                    const inputs = qsa('input, textarea, select, [contenteditable="true"]', document);
                    for (const el of inputs) {
                        const label = (el.labels?.[0]?.innerText || '').trim().toLowerCase();
                        const name = (el.getAttribute('name') || '').toLowerCase();
                        const placeholder = (el.getAttribute('placeholder') || '').toLowerCase();
                        const ariaLabel = (el.getAttribute('aria-label') || '').toLowerCase();
                        if ((label && label.includes(target)) || (name && name.includes(target)) || (placeholder && placeholder.includes(target)) || (ariaLabel && ariaLabel.includes(target))) {
                            el.focus();
                            el.value = text;
                            el.dispatchEvent(new Event('input', {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                            return true;
                        }
                    }
                    return false;
                }
                """,
                {"target": target, "text": text},
            )
            if filled:
                return
        except Exception:
            pass

        raise RuntimeError(f"Could not find input element for: {target}")
