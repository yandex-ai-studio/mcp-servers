# MCP Servers in Yandex AI Studio

This directory contains extracted MCP server packages and shared deployment scripts.

## Architecture

Each MCP gateway tool forwards requests to one or more Yandex Cloud backends:

1. Client calls MCP gateway tool.
2. MCP gateway performs `functionCall`, `httpCall`, or another supported action.
3. The selected backend executes business logic and returns JSON response.

`deploy/` contains shared scripts used by all servers.
`servers/` contains isolated MCP server packages.

## Servers

- [YandexART](servers/yandexart/README.md)  
  Generates images with YandexART and returns `image_url` (inline data URL or bucket URL).
- [Grapher](servers/grapher/README.md)  
  Renders bar/line/pie charts and returns `image_url` (inline data URL or bucket URL).
- [arXiv](servers/arxiv/README.md)  
  Provides search and paper-details tools via two cloud functions under one gateway.
- [Webdriver](servers/webdriver/README.md)  
  Fetches rendered web pages with Playwright/Chromium from a private serverless container.

## Deployment Procedure

Prerequisites:
- `yc` CLI configured for target cloud/folder
- access to service account IDs, backend IDs/URLs in specs, and auth values for MCP deploy

Typical flow per function-backed server:

1. Configure function settings in server `config.yaml` (and arXiv `config-search.yaml` + `config-get.yaml`).
2. Deploy function(s):
   - `servers\yandexart\funcdeploy.ps1`
   - `servers\grapher\funcdeploy.ps1`
   - `servers\arxiv\funcdeploy.ps1`
3. Update tool spec function IDs if needed.
4. Deploy MCP gateway from server directory:
   - `.\mcpdeploy.ps1 --gateway-name <gateway-name>`

For the browser-backed webdriver server, use the container install flow instead:

1. Configure container settings in `servers\webdriver\config.yaml`.
2. Build/push the image, deploy the private serverless container, resolve the MCP spec URL, and register the MCP gateway:
   - `servers\webdriver\installdeploy.ps1 --gateway-name webdriver`

Wrappers in each server call shared scripts in `deploy\` using relative path `..\..\deploy`.
