# Grapher MCP Server

## Overview

This server packages one Yandex Cloud Function that renders `bar`, `line`, and `pie` charts with `matplotlib`.
Success response format is always:

```json
{
  "image_url": "..."
}
```

`image_url` contains:
- data URL (`data:image/png;base64,...`) when `mode=inline`
- public Object Storage URL when `mode=bucket`

## Directory Layout

- `func/` - function source (`index.py`, `requirements.txt`)
- `config.yaml` - active function deployment config
- `config-sample.yaml` - sample function deployment config
- `grapher-tool.yaml` - MCP tool spec for gateway deployment
- `funcdeploy.ps1` - wrapper for `..\..\deploy\funcdeploy.ps1`
- `mcpdeploy.ps1` - wrapper for `..\..\deploy\mcpdeploy.ps1`

## Function Request Parameters

- `graph_type` (required): `bar`, `line`, or `pie`
- `data` (required): graph data object for the selected graph type
- `options` (optional): `title`, `xlabel`, `ylabel`, `width`, `height`, `dpi`

Environment parameters in `config.yaml`:
- `mode`: `inline` or `bucket`
- `static_key_id`, `static_key_secret`, `bucket` (required only for `mode=bucket`)

## Deploy

Deploy function:

```powershell
cd NEW_ROOT\servers\grapher
.\funcdeploy.ps1
```

Deploy MCP gateway (uses local `grapher-tool.yaml` by default):

```powershell
cd NEW_ROOT\servers\grapher
.\mcpdeploy.ps1 --gateway-name grapher --env-file .env
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
