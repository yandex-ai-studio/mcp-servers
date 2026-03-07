# arXiv MCP Server

## Overview

This server packages two Yandex Cloud Functions behind one MCP gateway:
- `search_arxiv` tool -> `func-search` cloud function
- `get_paper_details` tool -> `func-get` cloud function

## Directory Layout

- `func-search/` - search function source (`index.py`, `requirements.txt`)
- `func-get/` - get-by-id function source (`index.py`, `requirements.txt`)
- `config-search.yaml`, `config-get.yaml` - active function deploy configs
- `config-search-sample.yaml`, `config-get-sample.yaml` - sample configs
- `arxiv-search-tool.yaml`, `arxiv-get-tool.yaml` - MCP tool specs
- `funcdeploy.ps1` - deploys both functions via `..\..\deploy\funcdeploy.ps1`
- `mcpdeploy.ps1` - deploys one gateway with both specs via `..\..\deploy\mcpdeploy.ps1`

## Function Parameters

Search function (`func-search`):
- `query` (required)
- `field` (optional): `all|title|abstract|author`
- `max_results` (optional): `1..25`
- `sort_by` (optional): `relevance|submittedDate|lastUpdatedDate`
- `sort_order` (optional): `ascending|descending`

Get function (`func-get`):
- `arxiv_id` (required), for example `1706.03762` or `arXiv:1706.03762`

Both functions return either success JSON or `{"error":"..."}` with HTTP status `200`.

## Deploy

Deploy both functions:

```powershell
cd NEW_ROOT\servers\arxiv
.\funcdeploy.ps1
```

Deploy MCP gateway (uses both local specs by default):

```powershell
cd NEW_ROOT\servers\arxiv
.\mcpdeploy.ps1 --gateway-name arxiv
```
