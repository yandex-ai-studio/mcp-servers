# Kaggle MCP Server

## Overview

This server packages one Yandex Cloud Function behind one MCP gateway:

- `search_kaggle_datasets` searches public Kaggle datasets and enriches the top results with dataset details and a bounded file list.

The implementation uses the official `kaggle` Python package and access-token authentication.

## Directory Layout

- `func-search/` - search function source (`index.py`, `requirements.txt`)
- `config-search.yaml` - active, ignored function deployment config
- `config-search-sample.yaml` - sample deployment config
- `kaggle-search-tool.yaml` - MCP tool specification
- `funcdeploy.ps1` - deploys the function and maps the local Kaggle token
- `mcpdeploy.ps1` - deploys the MCP gateway
- `tests/` - handler and deployment tests

## Tool Parameters

- `query` (required)
- `max_results` (optional): `1..5`, default `3`
- `max_files` (optional): `1..100`, default `20`
- `sort_by` (optional): `hottest|votes|updated|active|published`
- `file_type` (optional): `all|csv|sqlite|json|bigQuery|parquet`
- `license` (optional): `all|cc|gpl|odb|other`
- `min_size`, `max_size` (optional): dataset size bounds in bytes

The tool exposes no pagination controls. It returns the top datasets and makes one file-list request per dataset. `files_truncated` indicates that Kaggle has more files than returned, without exposing a page token. File `columns` are included only when Kaggle supplies them.

## Authentication

Create `servers/kaggle/.env` with the access token generated from Kaggle's API settings:

```dotenv
kaggle_token=YOUR_KAGGLE_ACCESS_TOKEN
```

The function deployment wrapper maps this local key to `KAGGLE_API_TOKEN`, the environment variable expected by the official client. The token is not placed in tracked configuration files.

For MCP gateway deployment, the same `.env` may also contain:

```dotenv
FOLDER_ID=b1gxxxxxxxxxxxxxxx
SERVICE_ACCOUNT_ID=ajexxxxxxxxxxxxxxx
```

## Deploy

Copy `config-search-sample.yaml` to `config-search.yaml` if needed and set the service account ID. Then deploy the function:

```powershell
cd servers\kaggle
.\funcdeploy.ps1
```

After placing the deployed function ID in `kaggle-search-tool.yaml`, deploy the gateway:

```powershell
.\mcpdeploy.ps1 --gateway-name kaggle --env-file .env
```

## Test

```powershell
python -m pytest servers\kaggle\tests
```
