param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Read-SimpleYamlScalars {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Config file not found: $Path"
    }

    $values = @{}
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        $parts = $line -split ":", 2
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

function Test-ArgPresent {
    param([string[]]$ArgsList, [string]$Name)

    foreach ($arg in $ArgsList) {
        if ($arg -eq $Name) { return $true }
    }
    return $false
}

function New-ResolvedSpec {
    param(
        [string]$TemplatePath,
        [string]$OutputDir,
        [string]$AppId
    )

    $name = [System.IO.Path]::GetFileName($TemplatePath)
    $outputPath = Join-Path $OutputDir $name
    $content = Get-Content -LiteralPath $TemplatePath -Raw
    $content = $content.Replace("REPLACE_WITH_OPENWEATHER_APPID", $AppId)
    Set-Content -LiteralPath $outputPath -Value $content -Encoding UTF8
    return $outputPath
}

$sharedDeployScript = Join-Path $PSScriptRoot "..\..\deploy\mcpdeploy.ps1"
if (-not (Test-Path -LiteralPath $sharedDeployScript)) {
    throw "Shared deploy script not found: $sharedDeployScript"
}

$configPath = Join-Path $PSScriptRoot "config.yaml"
$cfg = Read-SimpleYamlScalars -Path $configPath
$appid = [string]$cfg["appid"]
if (-not $appid.Trim()) {
    throw "config.yaml must define appid"
}

$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("weather-mcp-specs-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempDir | Out-Null

try {
    $templates = @(
        (Join-Path $PSScriptRoot "weather-current-tool.yaml"),
        (Join-Path $PSScriptRoot "weather-forecast-tool.yaml"),
        (Join-Path $PSScriptRoot "weather-find-tool.yaml")
    )

    $argsToPass = @()
    if (-not (Test-ArgPresent -ArgsList $CliArgs -Name "--spec")) {
        foreach ($template in $templates) {
            $argsToPass += "--spec"
            $argsToPass += (New-ResolvedSpec -TemplatePath $template -OutputDir $tempDir -AppId $appid)
        }
    }

    if (-not (Test-ArgPresent -ArgsList $CliArgs -Name "--gateway-name")) {
        $gatewayName = [string]$cfg["gateway_name"]
        if (-not $gatewayName.Trim()) { $gatewayName = "weather" }
        $argsToPass += "--gateway-name"
        $argsToPass += $gatewayName
    }

    if (-not (Test-ArgPresent -ArgsList $CliArgs -Name "--gateway-description")) {
        $gatewayDescription = [string]$cfg["gateway_description"]
        if ($gatewayDescription.Trim()) {
            $argsToPass += "--gateway-description"
            $argsToPass += $gatewayDescription
        }
    }

    if (-not (Test-ArgPresent -ArgsList $CliArgs -Name "--env-file")) {
        $envFile = [string]$cfg["env_file"]
        if ($envFile.Trim()) {
            $argsToPass += "--env-file"
            if ([System.IO.Path]::IsPathRooted($envFile)) {
                $argsToPass += $envFile
            }
            else {
                $argsToPass += (Join-Path $PSScriptRoot $envFile)
            }
        }
    }

    if ($CliArgs) {
        $argsToPass += $CliArgs
    }
    & $sharedDeployScript @argsToPass
    if ($LASTEXITCODE -ne 0) {
        throw "MCP deployment failed"
    }
}
finally {
    if (Test-Path -LiteralPath $tempDir) {
        Remove-Item -LiteralPath $tempDir -Recurse -Force
    }
}
