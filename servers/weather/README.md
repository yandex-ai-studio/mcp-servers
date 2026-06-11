# Weather MCP Server

## Overview

This server exposes OpenWeatherMap city-name weather tools through Yandex AI Studio MCP Gateway direct HTTPS calls.
It does not deploy a Cloud Function or container. The gateway calls OpenWeatherMap endpoints directly.

The MCP tools are:

- `get_current_weather` - current weather by city name
- `get_weather_forecast` - 5 day / 3 hour forecast by city name
- `find_weather_cities` - city search results with current weather for each match

The tools use OpenWeatherMap's deprecated built-in city-name geocoder with the `q` query parameter.

## Directory Layout

- `config.yaml` - gateway defaults and OpenWeatherMap appid used during deployment. You need to put OpenWeatherMap appid in this file.
- `weather-current-tool.yaml` - current weather MCP tool template
- `weather-forecast-tool.yaml` - forecast MCP tool template
- `weather-find-tool.yaml` - city search MCP tool template
- `deploy.ps1` - wrapper that injects appid into temporary specs and calls `..\..\deploy\mcpdeploy.ps1`

## Tool Parameters

All tools require:

- `city`: city query string, for example `London`, `London,GB`, or `London,Ontario,CA`

Optional parameters:

- `units`: `standard`, `metric`, or `imperial`
- `lang`: OpenWeatherMap language code for weather descriptions
- `cnt`: result count for forecast/search tools
- `type`: `like` or `accurate` for `find_weather_cities`

## Deploy

Direct MCP gateway deployment requires:

- `FOLDER_ID`
- `SERVICE_ACCOUNT_ID`

Provide them either as environment variables or in `.env` in this directory:

```dotenv
FOLDER_ID=b1gxxxxxxxxxxxxxxx
SERVICE_ACCOUNT_ID=ajexxxxxxxxxxxxxxx
```

Deploy with:

```powershell
cd servers\weather
.\deploy.ps1
```

The wrapper reads `config.yaml`, writes temporary resolved specs with the configured `appid`, and calls the shared deployment script:

```powershell
..\..\deploy\mcpdeploy.ps1
```

You can pass shared deploy options through the wrapper:

```powershell
.\deploy.ps1 --gateway-name weather-test --dry-run --verbose
```

## Config

`config.yaml` contains:

```yaml
gateway_name: weather
gateway_description: OpenWeatherMap city-name weather tools.
env_file: .env
appid: <app_id>
```

The `appid` is embedded into generated deployment-time specs as a static query parameter.
