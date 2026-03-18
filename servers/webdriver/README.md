# Webdriver MCP Server

## Overview

This server packages a private Yandex Serverless Container that runs headless Chromium via Playwright.
It is intended for public-page fetching where a simple HTTP client is not enough because the target page
needs JavaScript execution or real browser rendering.

The MCP tool is:

- `fetch_web_pages`

By default, each result returns:

```json
{
  "requested_url": "https://example.com",
  "final_url": "https://example.com/",
  "status": 200,
  "title": "Example Domain",
  "text": "Example Domain ..."
}
```

Optional fields:

- `html` when `return_html=true` and container policy allows HTML output
- `screenshot_base64` when `return_screenshot=true` and container policy allows screenshots

Failures are reported per URL:

```json
{
  "requested_url": "https://bad.example",
  "error": "..."
}
```

## Why Serverless Container

Playwright + Chromium is heavier than the existing function-backed servers in this repo.
Using Yandex Serverless Containers avoids the packaging/runtime constraints of Cloud Functions while
still keeping deployment serverless and easy to automate.

## Directory Layout

- `container/` - FastAPI app, Playwright dependencies, and Dockerfile
- `config.yaml` - active container deployment config
- `config-sample.yaml` - sample deployment config
- `webdriver-tool.yaml` - MCP tool spec template with placeholder container URL
- `containerdeploy.ps1` - wrapper for `..\..\deploy\containerdeploy.ps1`
- `mcpdeploy.ps1` - wrapper for `..\..\deploy\mcpdeploy.ps1`
- `installdeploy.ps1` - full install flow: build image, deploy container, resolve spec, register MCP gateway

## Tool Parameters

- `urls` (required): array of 1..5 absolute HTTP/HTTPS URLs
- `timeout_ms` (optional): per-page timeout in milliseconds
- `wait_until` (optional): `load`, `domcontentloaded`, or `networkidle`
- `wait_for_selector` (optional): CSS selector to wait for after navigation
- `wait_after_load_ms` (optional): extra fixed delay after page load
- `return_html` (optional): include rendered HTML
- `return_screenshot` (optional): include full-page screenshot as base64 PNG

## Config

`config-sample.yaml` contains:

- `container_name`
- `service_account_id`
- `registry_name`
- `repository_name`
- `image_tag`
- `memory`
- `timeout`
- `concurrency`
- `source_dir`
- `dockerfile`
- `environment`

Important environment defaults:

- `DEFAULT_TIMEOUT_MS=15000`
- `DEFAULT_WAIT_UNTIL=domcontentloaded`
- `DEFAULT_WAIT_AFTER_LOAD_MS=1000`
- `MAX_URLS=5`
- `RETURN_HTML_BY_DEFAULT=false`
- `RETURN_SCREENSHOT_BY_DEFAULT=false`
- `ALLOW_HTML=true`
- `ALLOW_SCREENSHOT=true`

## Deploy

1. Copy `config-sample.yaml` to `config.yaml` and fill in your values.
2. Run the full install flow:

```powershell
cd NEW_ROOT\servers\webdriver
.\installdeploy.ps1 --gateway-name webdriver
```

If you need the steps separately:

```powershell
cd NEW_ROOT\servers\webdriver
.\containerdeploy.ps1
.\mcpdeploy.ps1 --spec .\webdriver-tool.yaml --gateway-name webdriver
```

The separate `mcpdeploy.ps1` flow only works after replacing the placeholder URL in `webdriver-tool.yaml`.
`installdeploy.ps1` handles that automatically without modifying the tracked spec template.

## Operational Notes

- v1 supports public, unauthenticated pages only.
- HTML and screenshot output can make responses large and slower.
- Chromium cold starts will be higher than the function-backed servers in this repo.
- Each request processes URLs sequentially inside one browser instance and reports partial failures.

