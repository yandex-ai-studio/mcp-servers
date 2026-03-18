param(
    [string]$Config = (Join-Path $PSScriptRoot "config.yaml"),
    [string]$ContainerName,
    [string]$ServiceAccountId,
    [string]$RegistryName,
    [string]$RepositoryName,
    [string]$ImageTag,
    [string]$Memory,
    [string]$Timeout,
    [string]$Concurrency,
    [string]$Cores,
    [string]$Dockerfile,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sharedDeployScript = Join-Path $PSScriptRoot "..\..\deploy\containerdeploy.ps1"
if (-not (Test-Path -LiteralPath $sharedDeployScript)) {
    throw "Shared deploy script not found: $sharedDeployScript"
}

$deployParams = @{
    Config = $Config
    DryRun = $DryRun
}

if ($ContainerName) { $deployParams.ContainerName = $ContainerName }
if ($ServiceAccountId) { $deployParams.ServiceAccountId = $ServiceAccountId }
if ($RegistryName) { $deployParams.RegistryName = $RegistryName }
if ($RepositoryName) { $deployParams.RepositoryName = $RepositoryName }
if ($ImageTag) { $deployParams.ImageTag = $ImageTag }
if ($Memory) { $deployParams.Memory = $Memory }
if ($Timeout) { $deployParams.Timeout = $Timeout }
if ($Concurrency) { $deployParams.Concurrency = $Concurrency }
if ($Cores) { $deployParams.Cores = $Cores }
if ($Dockerfile) { $deployParams.Dockerfile = $Dockerfile }

& $sharedDeployScript @deployParams
if ($LASTEXITCODE -ne 0) {
    throw "Deployment failed"
}
