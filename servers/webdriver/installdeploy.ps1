param(
    [string]$Config = (Join-Path $PSScriptRoot "config.yaml"),
    [string]$GatewayName = "webdriver",
    [string]$GatewayDescription = "Webdriver MCP server backed by a private serverless container running Playwright.",
    [switch]$DryRun,
    [switch]$Verbose
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found in PATH: $Name"
    }
}

function Read-YamlFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Config file not found: $Path"
    }

    if (Get-Command ConvertFrom-Yaml -ErrorAction SilentlyContinue) {
        $raw = Get-Content -LiteralPath $Path -Raw
        $obj = $raw | ConvertFrom-Yaml
        if (-not $obj) {
            throw "Config file is empty or invalid YAML: $Path"
        }
        return $obj
    }

    Require-Command -Name python
    $json = & python -c "import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1],'r',encoding='utf-8').read()) or {}))" $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to parse YAML config with Python: $Path"
    }
    $parsed = $json | ConvertFrom-Json
    if (-not $parsed) {
        throw "Config file is empty or invalid YAML: $Path"
    }
    return $parsed
}

function Get-RequiredConfigValue {
    param(
        [object]$ConfigObject,
        [string]$Key
    )

    $value = $ConfigObject.$Key
    if (-not ($value -is [string]) -or -not $value.Trim()) {
        throw "$Key is required in $Config"
    }
    return $value.Trim()
}

$containerDeployScript = Join-Path $PSScriptRoot "containerdeploy.ps1"
$sharedMcpDeployScript = Join-Path $PSScriptRoot "..\..\deploy\mcpdeploy.ps1"
$templateSpecPath = Join-Path $PSScriptRoot "webdriver-tool.yaml"

foreach ($requiredPath in @($containerDeployScript, $sharedMcpDeployScript, $templateSpecPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required file not found: $requiredPath"
    }
}

$cfg = Read-YamlFile -Path (Resolve-Path -LiteralPath $Config).Path
$containerName = Get-RequiredConfigValue -ConfigObject $cfg -Key "container_name"
$containerMetadataPath = Join-Path $PSScriptRoot "$containerName.container.json"
$resolvedSpecPath = Join-Path $PSScriptRoot ".webdriver-tool.resolved.yaml"

Write-Host "Deploying serverless container..."
$containerDeployParams = @{
    Config = $Config
    DryRun = $DryRun
}
& $containerDeployScript @containerDeployParams
if ($LASTEXITCODE -ne 0) {
    throw "Container deployment failed"
}

if ($DryRun) {
    $containerUrl = "https://dryrun.containers.yandexcloud.net/"
}
else {
    if (-not (Test-Path -LiteralPath $containerMetadataPath)) {
        throw "Container metadata file not found: $containerMetadataPath"
    }
    $containerMetadata = Get-Content -LiteralPath $containerMetadataPath -Raw | ConvertFrom-Json
    $containerUrl = [string]$containerMetadata.container_url
    if (-not $containerUrl) {
        throw "Container invoke URL not found in metadata: $containerMetadataPath"
    }
}

$resolvedSpec = (Get-Content -LiteralPath $templateSpecPath -Raw).Replace("REPLACE_WITH_CONTAINER_URL", $containerUrl)
Set-Content -LiteralPath $resolvedSpecPath -Value $resolvedSpec -Encoding UTF8

try {
    Write-Host "Registering MCP gateway '$GatewayName'..."
    $mcpArgs = @(
        "--spec", $resolvedSpecPath,
        "--gateway-name", $GatewayName,
        "--gateway-description", $GatewayDescription
    )
    if ($DryRun) { $mcpArgs += "--dry-run" }
    if ($Verbose) { $mcpArgs += "--verbose" }

    & $sharedMcpDeployScript @mcpArgs
    if ($LASTEXITCODE -ne 0) {
        throw "MCP deployment failed"
    }
}
finally {
    if (Test-Path -LiteralPath $resolvedSpecPath) {
        Remove-Item -LiteralPath $resolvedSpecPath -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Install flow finished."
if (-not $DryRun) {
    Write-Host "Container metadata: $containerMetadataPath"
    Write-Host "Gateway metadata: $(Join-Path $PSScriptRoot "$GatewayName.created.json")"
}
