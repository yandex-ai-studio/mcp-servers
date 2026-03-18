# YandexART MCP Server

## Overview

This server packages one Yandex Cloud Function that generates images with YandexART.
Success response format is always:

```json
{
  "image_url": "..."
}
```

`image_url` contains:
- data URL (`data:image/...;base64,...`) when `mode=inline`
- public Object Storage URL when `mode=bucket`

## Directory Layout

- `func/` - function source (`index.py`, `requirements.txt`)
- `config.yaml` - active function deployment config
- `config-sample.yaml` - sample function deployment config
- `yart-tool.yaml` - MCP tool spec for gateway deployment
- `funcdeploy.ps1` - wrapper for `..\..\deploy\funcdeploy.ps1`
- `mcpdeploy.ps1` - wrapper for `..\..\deploy\mcpdeploy.ps1`

## Function Request Parameters

- `prompt` (required): image prompt text
- `folder_id` (optional): Yandex Cloud folder ID
- `api_key` (optional): Yandex AI Studio API key
- `width_ratio` (optional): positive integer, default `1`
- `height_ratio` (optional): positive integer, default `1`

Credential resolution:
- request `folder_id`/`api_key` override environment values
- if omitted, function uses `YART_FOLDER_ID`/`YART_API_KEY`

Environment parameters in `config.yaml`:
- `mode`: `inline` or `bucket`
- `YART_FOLDER_ID`, `YART_API_KEY`
- `DOWNSIZE_FACTOR` (fractional `>= 1`, default `1`)
- `static_key_id`, `static_key_secret`, `bucket` (required only for `mode=bucket`)

## Deploy

Deploy function:

```powershell
cd NEW_ROOT\servers\yandexart
.\funcdeploy.ps1
```

Deploy MCP gateway (uses local `yart-tool.yaml` by default):

```powershell
cd NEW_ROOT\servers\yandexart
.\mcpdeploy.ps1 --gateway-name yandexart --env-file .env
```

`mcpdeploy.ps1` requires MCP gateway deployment settings:
- `FOLDER_ID`
- `SERVICE_ACCOUNT_ID`

Provide them either as environment variables or in `.env` in this directory:

```dotenv
FOLDER_ID=b1gxxxxxxxxxxxxxxx
SERVICE_ACCOUNT_ID=ajexxxxxxxxxxxxxxx
```

Authentication is resolved from `IAM_TOKEN`, then `yc iam create-token`, then `API_KEY`.
