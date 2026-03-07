param(
    [string]$Config = (Join-Path $PSScriptRoot "config.yaml"),
    [string]$FunctionName,
    [string]$ServiceAccountId,
    [string]$Runtime,
    [string]$Entrypoint,
    [string]$Memory,
    [string]$Timeout
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sharedDeployScript = Join-Path $PSScriptRoot "..\..\deploy\funcdeploy.ps1"
if (-not (Test-Path -LiteralPath $sharedDeployScript)) {
    throw "Shared deploy script not found: $sharedDeployScript"
}

$deployParams = @{
    Config = $Config
}

if ($FunctionName) { $deployParams.FunctionName = $FunctionName }
if ($ServiceAccountId) { $deployParams.ServiceAccountId = $ServiceAccountId }
if ($Runtime) { $deployParams.Runtime = $Runtime }
if ($Entrypoint) { $deployParams.Entrypoint = $Entrypoint }
if ($Memory) { $deployParams.Memory = $Memory }
if ($Timeout) { $deployParams.Timeout = $Timeout }

& $sharedDeployScript @deployParams
if ($LASTEXITCODE -ne 0) {
    throw "Deployment failed"
}
