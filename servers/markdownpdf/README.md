# MarkdownPDF MCP Server

## Overview

This server packages one Yandex Cloud Function that renders Markdown into PDF with `simpdf`.
Success response format is always:

```json
{
  "pdf_url": "..."
}
```

`pdf_url` contains:
- data URL (`data:application/pdf;base64,...`) when `mode=inline`
- public Object Storage URL when `mode=bucket`

The function expects DejaVu Sans TTF files to be available in an Object Storage bucket mounted into the function filesystem.
By default it reads fonts from `/function/storage/fonts`.

Required font files in the mounted directory:
- `DejaVuSans.ttf`
- `DejaVuSans-Bold.ttf`
- `DejaVuSans-Oblique.ttf`
- `DejaVuSans-BoldOblique.ttf`

## Directory Layout

- `func/` - function source (`index.py`, `requirements.txt`)
- `config.yaml` - active function deployment config
- `config-sample.yaml` - sample function deployment config
- `markdownpdf-tool.yaml` - MCP tool spec for gateway deployment
- `funcdeploy.ps1` - wrapper for `..\..\deploy\funcdeploy.ps1`
- `mcpdeploy.ps1` - wrapper for `..\..\deploy\mcpdeploy.ps1`

## Function Request Parameters

- `markdown` (required): Markdown text to render

Environment parameters in `config.yaml`:
- `mode`: `inline` or `bucket`
- `MARKDOWNPDF_FONT_DIR`: mounted font directory path, default `/function/storage/fonts`
- `static_key_id`, `static_key_secret`, `bucket` (required only for `mode=bucket`)

Mounted bucket parameters in `config.yaml`:
- `mounts[0].bucket`: `sysbucket`
- `mounts[0].prefix`: `fonts`
- `mounts[0].mount_point`: `fonts`
- `mounts[0].type`: `object-storage`
- `mounts[0].mode`: `ro`

## Font Mount Setup

The deployment script creates the font mount automatically from `config.yaml`.
It mounts bucket `sysbucket`, folder `fonts`, at mount point `fonts`, which is exposed inside the function as `/function/storage/fonts`.

The deploy wrapper validates that:
- `MARKDOWNPDF_FONT_DIR` matches the configured mount point
- the font mount bucket differs from the output `bucket`

## Deploy

Deploy function:

```powershell
cd NEW_ROOT\servers\markdownpdf
.\funcdeploy.ps1
```

Deploy MCP gateway (uses local `markdownpdf-tool.yaml` by default):

```powershell
cd NEW_ROOT\servers\markdownpdf
.\mcpdeploy.ps1 --gateway-name markdownpdf --env-file .env
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
