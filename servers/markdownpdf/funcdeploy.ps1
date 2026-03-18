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

function Get-ObjectValue {
    param(
        [object]$Object,
        [string]$Key
    )
    if ($null -eq $Object) {
        return $null
    }
    if ($Object -is [System.Collections.IDictionary]) {
        return $Object[$Key]
    }

    $property = $Object.PSObject.Properties[$Key]
    if ($property) {
        return $property.Value
    }
    return $null
}

function Assert-MarkdownPdfConfig {
    param([object]$ConfigObject)

    $environment = Get-ObjectValue -Object $ConfigObject -Key "environment"
    $fontDirRaw = [string](Get-ObjectValue -Object $environment -Key "MARKDOWNPDF_FONT_DIR")
    $outputBucketRaw = [string](Get-ObjectValue -Object $environment -Key "bucket")

    $fontDir = $fontDirRaw.Trim()
    $outputBucket = $outputBucketRaw.Trim()
    if (-not $fontDir) {
        throw "environment.MARKDOWNPDF_FONT_DIR is required for markdownpdf deployment"
    }

    $fontMount = $null
    foreach ($mount in @((Get-ObjectValue -Object $ConfigObject -Key "mounts"))) {
        if ($null -eq $mount) {
            continue
        }

        $mountPoint = ([string](Get-ObjectValue -Object $mount -Key "mount_point")).Trim()
        $prefix = ([string](Get-ObjectValue -Object $mount -Key "prefix")).Trim()
        if ($mountPoint -eq "fonts" -and $prefix -eq "fonts") {
            $fontMount = $mount
            break
        }
    }

    if ($null -eq $fontMount) {
        throw "markdownpdf deployment requires a mounts entry with mount_point 'fonts' and prefix 'fonts'"
    }

    $mountPointResolved = ([string](Get-ObjectValue -Object $fontMount -Key "mount_point")).Trim()
    $mountBucketResolved = ([string](Get-ObjectValue -Object $fontMount -Key "bucket")).Trim()
    $expectedFontDir = "/function/storage/$mountPointResolved"

    if ($fontDir -ne $expectedFontDir) {
        throw "environment.MARKDOWNPDF_FONT_DIR must equal '$expectedFontDir' for the configured font mount"
    }
    if ($outputBucket -and $mountBucketResolved -and $outputBucket -eq $mountBucketResolved) {
        throw "font mount bucket must differ from environment.bucket"
    }
}

$sharedDeployScript = Join-Path $PSScriptRoot "..\..\deploy\funcdeploy.ps1"
if (-not (Test-Path -LiteralPath $sharedDeployScript)) {
    throw "Shared deploy script not found: $sharedDeployScript"
}

$resolvedConfig = Resolve-Path -LiteralPath $Config
$cfg = Read-YamlFile -Path $resolvedConfig
Assert-MarkdownPdfConfig -ConfigObject $cfg

$deployParams = @{
    Config = $resolvedConfig
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
