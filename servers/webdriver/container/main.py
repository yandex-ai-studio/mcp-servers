import base64
import os
import re
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


ALLOWED_WAIT_UNTIL = {"load", "domcontentloaded", "networkidle"}
DEFAULT_TIMEOUT_MS = 15000
DEFAULT_WAIT_UNTIL = "domcontentloaded"
DEFAULT_WAIT_AFTER_LOAD_MS = 1000
DEFAULT_MAX_URLS = 5
DEFAULT_ALLOW_HTML = True
DEFAULT_ALLOW_SCREENSHOT = True
DEFAULT_RETURN_HTML = False
DEFAULT_RETURN_SCREENSHOT = False
WHITESPACE_RE = re.compile(r"\s+")

app = FastAPI(title="webdriver-mcp-server")


def _read_bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _read_int_env(key: str, default: int, minimum: int) -> int:
    raw = os.getenv(key)
    if raw is None or not raw.strip():
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{key} must be an integer") from exc

    if value < minimum:
        raise RuntimeError(f"{key} must be >= {minimum}")
    return value


def _runtime_config() -> dict[str, Any]:
    default_wait_until = os.getenv("DEFAULT_WAIT_UNTIL", DEFAULT_WAIT_UNTIL).strip().lower()
    if default_wait_until not in ALLOWED_WAIT_UNTIL:
        raise RuntimeError(
            "DEFAULT_WAIT_UNTIL must be one of: load, domcontentloaded, networkidle"
        )

    return {
        "default_timeout_ms": _read_int_env("DEFAULT_TIMEOUT_MS", DEFAULT_TIMEOUT_MS, 1),
        "default_wait_until": default_wait_until,
        "default_wait_after_load_ms": _read_int_env(
            "DEFAULT_WAIT_AFTER_LOAD_MS", DEFAULT_WAIT_AFTER_LOAD_MS, 0
        ),
        "max_urls": _read_int_env("MAX_URLS", DEFAULT_MAX_URLS, 1),
        "allow_html": _read_bool_env("ALLOW_HTML", DEFAULT_ALLOW_HTML),
        "allow_screenshot": _read_bool_env("ALLOW_SCREENSHOT", DEFAULT_ALLOW_SCREENSHOT),
        "return_html_by_default": _read_bool_env(
            "RETURN_HTML_BY_DEFAULT", DEFAULT_RETURN_HTML
        ),
        "return_screenshot_by_default": _read_bool_env(
            "RETURN_SCREENSHOT_BY_DEFAULT", DEFAULT_RETURN_SCREENSHOT
        ),
    }


def _normalize_text(text: str) -> str:
    normalized = WHITESPACE_RE.sub(" ", text).strip()
    return normalized


def _validate_url(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("each URL must be a non-empty string")

    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"unsupported URL '{value}', only absolute http/https URLs are allowed")
    return value.strip()


def _read_bool_field(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"'{key}' must be a boolean when provided")


def _read_non_negative_int_field(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer when provided")
    if value < 0:
        raise ValueError(f"'{key}' must be >= 0")
    return value


def _read_positive_int_field(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"'{key}' must be an integer when provided")
    if value <= 0:
        raise ValueError(f"'{key}' must be > 0")
    return value


def _validated_request(payload: Any, config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("request payload must be a JSON object")

    urls = payload.get("urls")
    if not isinstance(urls, list) or not urls:
        raise ValueError("'urls' is required and must be a non-empty array")
    if len(urls) > config["max_urls"]:
        raise ValueError(f"'urls' supports at most {config['max_urls']} item(s)")

    normalized_urls = [_validate_url(item) for item in urls]

    wait_until = payload.get("wait_until", config["default_wait_until"])
    if not isinstance(wait_until, str) or wait_until not in ALLOWED_WAIT_UNTIL:
        raise ValueError("'wait_until' must be one of: load, domcontentloaded, networkidle")

    wait_for_selector = payload.get("wait_for_selector")
    if wait_for_selector is not None:
        if not isinstance(wait_for_selector, str) or not wait_for_selector.strip():
            raise ValueError("'wait_for_selector' must be a non-empty string when provided")
        wait_for_selector = wait_for_selector.strip()

    return_html = _read_bool_field(payload, "return_html", config["return_html_by_default"])
    return_screenshot = _read_bool_field(
        payload,
        "return_screenshot",
        config["return_screenshot_by_default"],
    )

    if return_html and not config["allow_html"]:
        raise ValueError("HTML output is disabled by container configuration")
    if return_screenshot and not config["allow_screenshot"]:
        raise ValueError("Screenshot output is disabled by container configuration")

    return {
        "urls": normalized_urls,
        "timeout_ms": _read_positive_int_field(
            payload,
            "timeout_ms",
            config["default_timeout_ms"],
        ),
        "wait_until": wait_until,
        "wait_for_selector": wait_for_selector,
        "wait_after_load_ms": _read_non_negative_int_field(
            payload,
            "wait_after_load_ms",
            config["default_wait_after_load_ms"],
        ),
        "return_html": return_html,
        "return_screenshot": return_screenshot,
    }


def _extract_page_text(page: Any) -> str:
    text = page.evaluate(
        """
        () => {
            if (!document.body) {
                return "";
            }
            return document.body.innerText || "";
        }
        """
    )
    return _normalize_text(text or "")


def _fetch_single_url(browser: Any, request_cfg: dict[str, Any], url: str) -> dict[str, Any]:
    page = browser.new_page()
    page.set_default_timeout(request_cfg["timeout_ms"])

    try:
        response = page.goto(url, wait_until=request_cfg["wait_until"], timeout=request_cfg["timeout_ms"])
        if request_cfg["wait_for_selector"]:
            page.wait_for_selector(
                request_cfg["wait_for_selector"],
                timeout=request_cfg["timeout_ms"],
            )
        if request_cfg["wait_after_load_ms"] > 0:
            page.wait_for_timeout(request_cfg["wait_after_load_ms"])

        result = {
            "requested_url": url,
            "final_url": page.url,
            "status": None if response is None else response.status,
            "title": page.title(),
            "text": _extract_page_text(page),
        }

        if request_cfg["return_html"]:
            result["html"] = page.content()
        if request_cfg["return_screenshot"]:
            screenshot = page.screenshot(full_page=True, type="png")
            result["screenshot_base64"] = base64.b64encode(screenshot).decode("ascii")

        return result
    except (PlaywrightTimeoutError, PlaywrightError, ValueError) as exc:
        return {
            "requested_url": url,
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover - defensive catch for remote runtime
        return {
            "requested_url": url,
            "error": f"internal error: {exc}",
        }
    finally:
        page.close()


def _process_request(payload: Any) -> dict[str, Any]:
    config = _runtime_config()
    request_cfg = _validated_request(payload, config)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            chromium_sandbox=False,
            args=[
                "--disable-background-networking",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-setuid-sandbox",
                "--no-sandbox",
                "--no-zygote",
                "--single-process",
            ],
        )
        try:
            results = [
                _fetch_single_url(browser, request_cfg, url)
                for url in request_cfg["urls"]
            ]
        finally:
            browser.close()

    return {"results": results}


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/")
async def fetch_web_pages(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc

    try:
        return await run_in_threadpool(_process_request, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


