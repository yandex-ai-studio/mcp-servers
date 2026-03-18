param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sharedDeployScript = Join-Path $PSScriptRoot "..\..\deploy\mcpdeploy.ps1"
if (-not (Test-Path -LiteralPath $sharedDeployScript)) {
    throw "Shared deploy script not found: $sharedDeployScript"
}

$hasSpec = $false
foreach ($arg in $CliArgs) {
    if ($arg -eq "--spec") {
        $hasSpec = $true
        break
    }
}

$argsToPass = @()
if (-not $hasSpec) {
    $argsToPass += "--spec"
    $argsToPass += (Join-Path $PSScriptRoot "markdownpdf-tool.yaml")
}
$argsToPass += $CliArgs

& $sharedDeployScript @argsToPass
if ($LASTEXITCODE -ne 0) {
    throw "MCP deployment failed"
}
