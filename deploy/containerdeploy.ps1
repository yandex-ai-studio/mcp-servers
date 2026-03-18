param(
    [Parameter(Mandatory = $true)]
    [string]$Config,
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

    $value = Get-ObjectPropertyValue -Obj $ConfigObject -Name $Key
    if ($value -is [string] -and $value.Trim()) {
        return $value.Trim()
    }

    if ($null -ne $value -and -not ($value -is [string])) {
        return [string]$value
    }

    return $null
}

function Get-ObjectPropertyValue {
    param(
        [object]$Obj,
        [string]$Name
    )

    if ($null -eq $Obj) {
        return $null
    }
    if ($Obj -is [System.Collections.IDictionary]) {
        if ($Obj.Contains($Name)) {
            return $Obj[$Name]
        }
        return $null
    }

    $prop = $Obj.PSObject.Properties[$Name]
    if ($null -eq $prop) {
        return $null
    }
    return $prop.Value
}

function Convert-ToStringMap {
    param([object]$Value)

    $map = @{}
    if ($null -eq $Value) {
        return $map
    }

    if ($Value -is [string] -or $Value -is [ValueType]) {
        throw "Expected an object/map for environment values"
    }

    if ($Value -is [System.Collections.IDictionary]) {
        $entries = $Value.GetEnumerator()
    }
    else {
        $entries = $Value.PSObject.Properties
    }

    foreach ($entry in $entries) {
        if ($entry -is [System.Collections.DictionaryEntry]) {
            $key = [string]$entry.Key
            $rawValue = $entry.Value
        }
        else {
            $key = [string]$entry.Name
            $rawValue = $entry.Value
        }

        $key = $key.Trim()
        if (-not $key) {
            continue
        }
        if ($null -eq $rawValue) {
            continue
        }

        $map[$key] = [string]$rawValue
    }

    return $map
}

function Get-EnvironmentArgs {
    param([object]$EnvironmentConfig)

    $args = @()
    $values = Convert-ToStringMap -Value $EnvironmentConfig
    foreach ($entry in $values.GetEnumerator()) {
        $args += "--environment"
        $args += "$($entry.Key)=$($entry.Value)"
    }
    return $args
}

function Invoke-ExternalCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [switch]$CaptureOutput,
        [switch]$AllowFailure
    )

    $printable = @($FilePath) + $Arguments
    Write-Host ("Running: " + ($printable -join " "))
    if ($DryRun) {
        return @{
            Success = $true
            ExitCode = 0
            Output = ""
        }
    }

    if ($CaptureOutput) {
        $output = & $FilePath @Arguments 2>&1 | Out-String
    }
    else {
        & $FilePath @Arguments
        $output = ""
    }

    $exitCode = $LASTEXITCODE
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "Command failed with exit code ${exitCode}: $($printable -join ' ')`n$output"
    }

    return @{
        Success = ($exitCode -eq 0)
        ExitCode = $exitCode
        Output = $output
    }
}

function Get-JsonPayloadFromText {
    param([string]$Text)

    if (-not $Text) {
        return ""
    }

    $trimmed = $Text.Trim()
    if (-not $trimmed) {
        return ""
    }

    $starts = @("{", "[")
    $startIndex = -1
    foreach ($candidate in $starts) {
        $idx = $trimmed.IndexOf($candidate)
        if ($idx -ge 0 -and ($startIndex -lt 0 -or $idx -lt $startIndex)) {
            $startIndex = $idx
        }
    }
    if ($startIndex -lt 0) {
        throw "No JSON payload found in command output: $trimmed"
    }

    $json = $trimmed.Substring($startIndex)
    $openChar = $json[0]
    $closeChar = if ($openChar -eq "{") { "}" } else { "]" }
    $depth = 0
    $inString = $false
    $escaped = $false

    for ($i = 0; $i -lt $json.Length; $i++) {
        $char = $json[$i]

        if ($inString) {
            if ($escaped) {
                $escaped = $false
                continue
            }
            if ($char -eq "\") {
                $escaped = $true
                continue
            }
            if ($char -eq '"') {
                $inString = $false
            }
            continue
        }

        if ($char -eq '"') {
            $inString = $true
            continue
        }
        if ($char -eq $openChar) {
            $depth += 1
            continue
        }
        if ($char -eq $closeChar) {
            $depth -= 1
            if ($depth -eq 0) {
                return $json.Substring(0, $i + 1)
            }
        }
    }

    throw "Could not isolate JSON payload from command output"
}

function Invoke-YcJson {
    param([string[]]$Arguments)

    $result = Invoke-ExternalCommand -FilePath "yc" -Arguments ($Arguments + @("--format", "json")) -CaptureOutput
    if (-not $result.Output.Trim()) {
        return @{}
    }

    try {
        $jsonText = Get-JsonPayloadFromText -Text $result.Output
        return ($jsonText | ConvertFrom-Json -Depth 100)
    }
    catch {
        throw "yc returned non-JSON output for command: yc $($Arguments -join ' ')`n$($result.Output)"
    }
}

function Try-GetYcJson {
    param([string[]]$Arguments)

    $result = Invoke-ExternalCommand -FilePath "yc" -Arguments ($Arguments + @("--format", "json")) -CaptureOutput -AllowFailure
    if (-not $result.Success) {
        return $null
    }
    if (-not $result.Output.Trim()) {
        return @{}
    }
    $jsonText = Get-JsonPayloadFromText -Text $result.Output
    return ($jsonText | ConvertFrom-Json -Depth 100)
}

Require-Command -Name yc
Require-Command -Name docker

$configPath = Resolve-Path -LiteralPath $Config
$configDir = Split-Path -Parent $configPath
$cfg = Read-YamlFile -Path $configPath

$containerNameResolved = Get-ConfigValue -ConfigObject $cfg -Key "container_name" -Override $ContainerName
$serviceAccountIdResolved = Get-ConfigValue -ConfigObject $cfg -Key "service_account_id" -Override $ServiceAccountId
$registryNameResolved = Get-ConfigValue -ConfigObject $cfg -Key "registry_name" -Override $RegistryName
$repositoryNameResolved = Get-ConfigValue -ConfigObject $cfg -Key "repository_name" -Override $RepositoryName
$imageTagResolved = Get-ConfigValue -ConfigObject $cfg -Key "image_tag" -Override $ImageTag
$memoryResolved = Get-ConfigValue -ConfigObject $cfg -Key "memory" -Override $Memory
$timeoutResolved = Get-ConfigValue -ConfigObject $cfg -Key "timeout" -Override $Timeout
$concurrencyResolved = Get-ConfigValue -ConfigObject $cfg -Key "concurrency" -Override $Concurrency
$coresResolved = Get-ConfigValue -ConfigObject $cfg -Key "cores" -Override $Cores
$dockerfileResolvedConfig = Get-ConfigValue -ConfigObject $cfg -Key "dockerfile" -Override $Dockerfile

if (-not $containerNameResolved) { throw "container_name is required (config or -ContainerName)." }
if (-not $serviceAccountIdResolved) { throw "service_account_id is required (config or -ServiceAccountId)." }
if (-not $registryNameResolved) { $registryNameResolved = "$containerNameResolved-registry" }
if (-not $repositoryNameResolved) { $repositoryNameResolved = $containerNameResolved }
if (-not $imageTagResolved) { $imageTagResolved = "latest" }
if (-not $memoryResolved) { $memoryResolved = "1GB" }
if (-not $timeoutResolved) { $timeoutResolved = "60s" }
if (-not $concurrencyResolved) { $concurrencyResolved = "1" }
if (-not $coresResolved) { $coresResolved = "1" }

$sourceDirRaw = [string](Get-ObjectPropertyValue -Obj $cfg -Name "source_dir")
if (-not $sourceDirRaw.Trim()) {
    $sourceDirRaw = "."
}
$sourceDirResolved = Resolve-Path -LiteralPath (Join-Path $configDir $sourceDirRaw)

if ($dockerfileResolvedConfig) {
    $dockerfilePath = Resolve-Path -LiteralPath (Join-Path $configDir $dockerfileResolvedConfig)
}
else {
    $dockerfilePath = Resolve-Path -LiteralPath (Join-Path $sourceDirResolved "Dockerfile")
}

$environmentArgs = Get-EnvironmentArgs -EnvironmentConfig (Get-ObjectPropertyValue -Obj $cfg -Name "environment")

Write-Host "Ensuring container registry '$registryNameResolved' exists..."
if ($DryRun) {
    [void](Invoke-ExternalCommand -FilePath "yc" -Arguments @("container", "registry", "get", "--name", $registryNameResolved) -CaptureOutput -AllowFailure)
    [void](Invoke-ExternalCommand -FilePath "yc" -Arguments @("container", "registry", "create", "--name", $registryNameResolved) -CaptureOutput)
    $registry = @{
        id = "dryrun-registry-id"
        name = $registryNameResolved
    }
}
else {
    $registry = Try-GetYcJson -Arguments @("container", "registry", "get", "--name", $registryNameResolved)
    if ($null -eq $registry) {
        $registry = Invoke-YcJson -Arguments @("container", "registry", "create", "--name", $registryNameResolved)
    }
}

$registryId = [string]$registry.id
if (-not $registryId) {
    throw "Failed to resolve registry ID for '$registryNameResolved'"
}

$imageUrl = "cr.yandex/$registryId/$repositoryNameResolved`:$imageTagResolved"

Write-Host "Configuring Docker authentication for Yandex Container Registry..."
[void](Invoke-ExternalCommand -FilePath "yc" -Arguments @("container", "registry", "configure-docker"))

Write-Host "Building Docker image..."
[void](Invoke-ExternalCommand -FilePath "docker" -Arguments @("build", "-f", $dockerfilePath, "-t", $imageUrl, $sourceDirResolved.Path))

Write-Host "Pushing Docker image..."
[void](Invoke-ExternalCommand -FilePath "docker" -Arguments @("push", $imageUrl))

Write-Host "Ensuring serverless container '$containerNameResolved' exists..."
if ($DryRun) {
    [void](Invoke-ExternalCommand -FilePath "yc" -Arguments @("serverless", "container", "get", "--name", $containerNameResolved) -CaptureOutput -AllowFailure)
    [void](Invoke-ExternalCommand -FilePath "yc" -Arguments @("serverless", "container", "create", "--name", $containerNameResolved) -CaptureOutput)
    $container = @{
        id = "dryrun-container-id"
        url = "https://dryrun.containers.yandexcloud.net/"
    }
}
else {
    $container = Try-GetYcJson -Arguments @("serverless", "container", "get", "--name", $containerNameResolved)
    if ($null -eq $container) {
        $container = Invoke-YcJson -Arguments @("serverless", "container", "create", "--name", $containerNameResolved)
    }
}

Write-Host "Deploying new revision for '$containerNameResolved'..."
$deployArgs = @(
    "serverless", "container", "revision", "deploy",
    "--container-name", $containerNameResolved,
    "--image", $imageUrl,
    "--cores", $coresResolved,
    "--memory", $memoryResolved,
    "--concurrency", $concurrencyResolved,
    "--execution-timeout", $timeoutResolved,
    "--service-account-id", $serviceAccountIdResolved,
    "--runtime", "http"
)
if ($environmentArgs.Count -gt 0) {
    $deployArgs += $environmentArgs
}
if ($DryRun) {
    [void](Invoke-ExternalCommand -FilePath "yc" -Arguments ($deployArgs + @("--format", "json")) -CaptureOutput)
    $revision = @{
        id = "dryrun-revision-id"
        status = "ACTIVE"
    }
}
else {
    $revision = Invoke-YcJson -Arguments $deployArgs
    $container = Invoke-YcJson -Arguments @("serverless", "container", "get", "--name", $containerNameResolved)
}

$metadata = [ordered]@{
    container_name = $containerNameResolved
    container_id = [string]$container.id
    container_url = [string]$container.url
    image_url = $imageUrl
    registry_name = $registryNameResolved
    registry_id = $registryId
    repository_name = $repositoryNameResolved
    revision_id = [string]$revision.id
    revision_status = [string]$revision.status
    service_account_id = $serviceAccountIdResolved
}

$metadataPath = Join-Path $configDir "$containerNameResolved.container.json"
if (-not $DryRun) {
    ($metadata | ConvertTo-Json -Depth 100) | Set-Content -LiteralPath $metadataPath -Encoding UTF8
}

Write-Host ""
Write-Host "Container deployment finished."
Write-Host "container_name: $containerNameResolved"
Write-Host "container_id: $($metadata.container_id)"
Write-Host "revision_id: $($metadata.revision_id)"
Write-Host "container_url: $($metadata.container_url)"
Write-Host "image_url: $imageUrl"
Write-Host "saved: $metadataPath"
