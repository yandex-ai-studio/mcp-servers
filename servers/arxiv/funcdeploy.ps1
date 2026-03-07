param(
    [string]$SearchConfig = (Join-Path $PSScriptRoot "config-search.yaml"),
    [string]$GetConfig = (Join-Path $PSScriptRoot "config-get.yaml"),
    [string]$Runtime,
    [string]$Entrypoint,
    [string]$Memory,
    [string]$Timeout,
    [string]$ServiceAccountId
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sharedDeployScript = Join-Path $PSScriptRoot "..\..\deploy\funcdeploy.ps1"
if (-not (Test-Path -LiteralPath $sharedDeployScript)) {
    throw "Shared deploy script not found: $sharedDeployScript"
}

$commonOverrides = @{}
if ($Runtime) { $commonOverrides.Runtime = $Runtime }
if ($Entrypoint) { $commonOverrides.Entrypoint = $Entrypoint }
if ($Memory) { $commonOverrides.Memory = $Memory }
if ($Timeout) { $commonOverrides.Timeout = $Timeout }
if ($ServiceAccountId) { $commonOverrides.ServiceAccountId = $ServiceAccountId }

Write-Host "Deploying arXiv search function..."
$searchParams = @{ Config = $SearchConfig } + $commonOverrides
& $sharedDeployScript @searchParams
if ($LASTEXITCODE -ne 0) {
    throw "Deployment failed for search function"
}

Write-Host "Deploying arXiv get function..."
$getParams = @{ Config = $GetConfig } + $commonOverrides
& $sharedDeployScript @getParams
if ($LASTEXITCODE -ne 0) {
    throw "Deployment failed for get function"
}

Write-Host "Both arXiv functions deployed."
