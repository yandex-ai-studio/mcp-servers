param(
    [Parameter(Mandatory = $true)]
    [string]$Config,
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

function Get-ConfigValue {
    param(
        [object]$ConfigObject,
        [string]$Key,
        [string]$Override
    )
    if ($Override) {
        return $Override
    }
    $value = Get-ObjectValue -Object $ConfigObject -Key $Key
    if ($value -is [string] -and $value.Trim()) {
        return $value.Trim()
    }
    return $null
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

function Invoke-Yc {
    param([string[]]$CommandArgs)
    & yc @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        throw "yc command failed: yc $($CommandArgs -join ' ')"
    }
}

function New-DeploymentArchive {
    param(
        [string]$SourceDir,
        [string[]]$IncludeFiles,
        [string]$NamePrefix
    )
    if (-not (Test-Path -LiteralPath $SourceDir)) {
        throw "source_dir does not exist: $SourceDir"
    }

    $tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("$NamePrefix-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tempDir | Out-Null

    foreach ($file in $IncludeFiles) {
        $trimmed = [string]$file
        if (-not $trimmed.Trim()) {
            continue
        }
        $sourcePath = Join-Path $SourceDir $trimmed
        if (-not (Test-Path -LiteralPath $sourcePath)) {
            throw "Required file not found for deployment archive: $trimmed"
        }

        $destPath = Join-Path $tempDir $trimmed
        $destDir = Split-Path -Parent $destPath
        if ($destDir -and -not (Test-Path -LiteralPath $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $sourcePath -Destination $destPath -Force
    }

    $zipPath = "$tempDir.zip"
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $tempDir "*") -DestinationPath $zipPath -Force

    return @{
        TempDir = $tempDir
        ZipPath = $zipPath
    }
}

function Get-EnvironmentArgs {
    param([object]$EnvironmentConfig)

    $args = @()
    if (-not $EnvironmentConfig) {
        return $args
    }

    if ($EnvironmentConfig -is [System.Collections.IDictionary]) {
        $entries = $EnvironmentConfig.GetEnumerator()
    }
    else {
        $entries = $EnvironmentConfig.PSObject.Properties
    }

    foreach ($entry in $entries) {
        if ($entry -is [System.Collections.DictionaryEntry]) {
            $key = [string]$entry.Key
            $value = $entry.Value
        }
        else {
            $key = [string]$entry.Name
            $value = $entry.Value
        }

        $key = $key.Trim()
        if (-not $key) {
            continue
        }
        if ($null -eq $value) {
            continue
        }

        $valueString = [string]$value
        $args += "--environment"
        $args += "$key=$valueString"
    }

    return $args
}

function Get-MountArgs {
    param([object]$MountsConfig)

    $args = @()
    if ($null -eq $MountsConfig) {
        return $args
    }

    $mountIndex = 0
    foreach ($mount in @($MountsConfig)) {
        if ($null -eq $mount) {
            continue
        }

        $mountIndex += 1
        $bucketRaw = [string](Get-ObjectValue -Object $mount -Key "bucket")
        $mountPointRaw = [string](Get-ObjectValue -Object $mount -Key "mount_point")
        $prefixRaw = [string](Get-ObjectValue -Object $mount -Key "prefix")
        $typeRaw = [string](Get-ObjectValue -Object $mount -Key "type")
        $modeRaw = [string](Get-ObjectValue -Object $mount -Key "mode")

        $bucket = $bucketRaw.Trim()
        $mountPoint = $mountPointRaw.Trim()
        $prefix = $prefixRaw.Trim()
        $type = $typeRaw.Trim()
        $mode = $modeRaw.Trim().ToLowerInvariant()

        if (-not $bucket) {
            throw "mounts[$mountIndex].bucket is required"
        }
        if (-not $mountPoint) {
            throw "mounts[$mountIndex].mount_point is required"
        }
        if (-not $type) {
            $type = "object-storage"
        }
        if (-not $mode) {
            $mode = "ro"
        }
        if ($mode -notin @("ro", "rw")) {
            throw "mounts[$mountIndex].mode must be 'ro' or 'rw'"
        }

        $mountSpecParts = @(
            "type=$type",
            "mount-point=$mountPoint",
            "bucket=$bucket"
        )
        if ($prefix) {
            $mountSpecParts += "prefix=$prefix"
        }
        $mountSpecParts += "mode=$mode"

        $args += "--mount"
        $args += ($mountSpecParts -join ",")
    }

    return $args
}

Require-Command -Name yc

$configPath = Resolve-Path -LiteralPath $Config
$configDir = Split-Path -Parent $configPath
$cfg = Read-YamlFile -Path $configPath

$functionNameResolved = Get-ConfigValue -ConfigObject $cfg -Key "function_name" -Override $FunctionName
$serviceAccountIdResolved = Get-ConfigValue -ConfigObject $cfg -Key "service_account_id" -Override $ServiceAccountId
$runtimeResolved = Get-ConfigValue -ConfigObject $cfg -Key "runtime" -Override $Runtime
$entrypointResolved = Get-ConfigValue -ConfigObject $cfg -Key "entrypoint" -Override $Entrypoint
$memoryResolved = Get-ConfigValue -ConfigObject $cfg -Key "memory" -Override $Memory
$timeoutResolved = Get-ConfigValue -ConfigObject $cfg -Key "timeout" -Override $Timeout

if (-not $runtimeResolved) { $runtimeResolved = "python314" }
if (-not $entrypointResolved) { $entrypointResolved = "index.handler" }
if (-not $memoryResolved) { $memoryResolved = "256m" }
if (-not $timeoutResolved) { $timeoutResolved = "20s" }

if (-not $functionNameResolved) { throw "function_name is required (config or -FunctionName)." }
if (-not $serviceAccountIdResolved) { throw "service_account_id is required (config or -ServiceAccountId)." }

$sourceDirRaw = [string](Get-ObjectValue -Object $cfg -Key "source_dir")
if (-not $sourceDirRaw.Trim()) {
    $sourceDirRaw = "."
}
$sourceDirResolved = Resolve-Path -LiteralPath (Join-Path $configDir $sourceDirRaw)

$includeFiles = @()
$includeFilesConfig = Get-ObjectValue -Object $cfg -Key "include_files"
if ($includeFilesConfig) {
    foreach ($f in $includeFilesConfig) {
        $includeFiles += [string]$f
    }
}
if ($includeFiles.Count -eq 0) {
    $includeFiles = @("index.py", "requirements.txt")
}
$environmentArgs = @(Get-EnvironmentArgs -EnvironmentConfig (Get-ObjectValue -Object $cfg -Key "environment"))
$mountArgs = @(Get-MountArgs -MountsConfig (Get-ObjectValue -Object $cfg -Key "mounts"))

Write-Host "Ensuring function '$functionNameResolved' exists..."
& yc serverless function get --name $functionNameResolved *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-Yc @("serverless", "function", "create", "--name", $functionNameResolved)
}

Write-Host "Packaging deployment archive from '$sourceDirResolved'..."
$archive = New-DeploymentArchive -SourceDir $sourceDirResolved -IncludeFiles $includeFiles -NamePrefix "funcdeploy"
Write-Host "Archive created: $($archive.ZipPath)"

try {
    Write-Host "Publishing new version for '$functionNameResolved'..."
    $versionCreateArgs = @(
        "serverless", "function", "version", "create",
        "--function-name", $functionNameResolved,
        "--runtime", $runtimeResolved,
        "--entrypoint", $entrypointResolved,
        "--memory", $memoryResolved,
        "--execution-timeout", $timeoutResolved,
        "--service-account-id", $serviceAccountIdResolved,
        "--source-path", $archive.ZipPath
    )
    if ($environmentArgs.Count -gt 0) {
        $versionCreateArgs += $environmentArgs
    }
    if ($mountArgs.Count -gt 0) {
        $versionCreateArgs += $mountArgs
    }
    Invoke-Yc $versionCreateArgs
}
finally {
    if ($archive -and $archive.TempDir -and (Test-Path -LiteralPath $archive.TempDir)) {
        Remove-Item -LiteralPath $archive.TempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($archive -and $archive.ZipPath -and (Test-Path -LiteralPath $archive.ZipPath)) {
        Remove-Item -LiteralPath $archive.ZipPath -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "Deployment finished."
Write-Host "Function info:"
Write-Host "  yc serverless function get --name $functionNameResolved"
Write-Host "Latest version info:"
Write-Host "  yc serverless function version list --function-name $functionNameResolved"



