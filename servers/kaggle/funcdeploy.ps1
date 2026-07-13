param(
    [string]$Config = (Join-Path $PSScriptRoot "config-search.yaml"),
    [string]$EnvFile = (Join-Path $PSScriptRoot ".env"),
    [string]$Runtime,
    [string]$Entrypoint,
    [string]$Memory,
    [string]$Timeout,
    [string]$ServiceAccountId
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Read-DotEnv {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }

        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) { continue }

        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if ($key) { $values[$key] = $value }
    }

    return $values
}

$sharedDeployScript = Join-Path $PSScriptRoot "..\..\deploy\funcdeploy.ps1"
if (-not (Test-Path -LiteralPath $sharedDeployScript)) {
    throw "Shared deploy script not found: $sharedDeployScript"
}

$dotenv = Read-DotEnv -Path $EnvFile
$token = [System.Environment]::GetEnvironmentVariable("KAGGLE_API_TOKEN")
if (-not $token) {
    $token = [string]$dotenv["KAGGLE_API_TOKEN"]
}
if (-not $token) {
    $token = [string]$dotenv["kaggle_token"]
}
if (-not $token -or -not $token.Trim()) {
    throw "Kaggle token not found. Set KAGGLE_API_TOKEN or kaggle_token in '$EnvFile'."
}

$deployParams = @{
    Config = $Config
    EnvironmentOverrides = @{ KAGGLE_API_TOKEN = $token.Trim() }
}
if ($Runtime) { $deployParams.Runtime = $Runtime }
if ($Entrypoint) { $deployParams.Entrypoint = $Entrypoint }
if ($Memory) { $deployParams.Memory = $Memory }
if ($Timeout) { $deployParams.Timeout = $Timeout }
if ($ServiceAccountId) { $deployParams.ServiceAccountId = $ServiceAccountId }

Write-Host "Deploying Kaggle dataset search function..."
& $sharedDeployScript @deployParams
if ($LASTEXITCODE -ne 0) {
    throw "Deployment failed for Kaggle dataset search function"
}

Write-Host "Kaggle dataset search function deployed."
