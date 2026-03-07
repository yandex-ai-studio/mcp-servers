$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$DEFAULT_MCP_API_BASE_URL = "https://serverless-mcp-gateway.api.cloud.yandex.net/mcpgateway/v1"
$DEFAULT_OPERATION_API_BASE_URL = "https://operation.api.cloud.yandex.net/operations"
$DEFAULT_GATEWAY_DESCRIPTION = "arXiv MCP server backed by separate search and get cloud functions."

function Show-Usage {
    Write-Host "Usage:"
    Write-Host "  pwsh mcpdeploy.ps1 --spec <path> [--spec <path> ...] --gateway-name <name> [options]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  --spec <path>                 Path to tool spec (.yaml/.yml/.json). Repeatable. Required."
    Write-Host "  --env-file <path>             Path to .env file. Default: .env"
    Write-Host "  --gateway-name <name>         MCP Gateway name. Required."
    Write-Host "  --gateway-description <text>  MCP Gateway description."
    Write-Host "  --dry-run                     Print first payload candidate and exit."
    Write-Host "  --verbose                     Verbose logging."
}

function Parse-Args {
    param([string[]]$RawArgs)

    $parsed = @{
        Spec = @()
        EnvFile = ".env"
        GatewayName = $null
        GatewayDescription = $DEFAULT_GATEWAY_DESCRIPTION
        DryRun = $false
        Verbose = $false
    }

    $i = 0
    while ($i -lt $RawArgs.Count) {
        $arg = $RawArgs[$i]
        switch ($arg) {
            "--spec" {
                if ($i + 1 -ge $RawArgs.Count) { throw "--spec requires a value" }
                $parsed.Spec += $RawArgs[$i + 1]
                $i += 2
                continue
            }
            "--env-file" {
                if ($i + 1 -ge $RawArgs.Count) { throw "--env-file requires a value" }
                $parsed.EnvFile = $RawArgs[$i + 1]
                $i += 2
                continue
            }
            "--gateway-name" {
                if ($i + 1 -ge $RawArgs.Count) { throw "--gateway-name requires a value" }
                $parsed.GatewayName = $RawArgs[$i + 1]
                $i += 2
                continue
            }
            "--gateway-description" {
                if ($i + 1 -ge $RawArgs.Count) { throw "--gateway-description requires a value" }
                $parsed.GatewayDescription = $RawArgs[$i + 1]
                $i += 2
                continue
            }
            "--dry-run" {
                $parsed.DryRun = $true
                $i += 1
                continue
            }
            "--verbose" {
                $parsed.Verbose = $true
                $i += 1
                continue
            }
            "--help" {
                Show-Usage
                exit 0
            }
            default {
                throw "Unknown argument: $arg"
            }
        }
    }

    if ($parsed.Spec.Count -eq 0) {
        throw "At least one --spec is required"
    }
    if (-not $parsed.GatewayName) {
        throw "--gateway-name is required"
    }
    return $parsed
}

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

function Build-Config {
    param([hashtable]$DotEnvValues)

    $cfg = @{}
    foreach ($entry in [System.Environment]::GetEnvironmentVariables().GetEnumerator()) {
        $cfg[[string]$entry.Key] = [string]$entry.Value
    }
    foreach ($entry in $DotEnvValues.GetEnumerator()) {
        $cfg[[string]$entry.Key] = [string]$entry.Value
    }
    return $cfg
}

function To-Bool {
    param([string]$Value, [bool]$Default = $true)

    if ($null -eq $Value) { return $Default }
    $low = $Value.Trim().ToLowerInvariant()
    if (@("1", "true", "yes", "y", "on") -contains $low) { return $true }
    if (@("0", "false", "no", "n", "off") -contains $low) { return $false }
    return $Default
}

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found in PATH: $Name"
    }
}

function Get-AuthHeader {
    param([hashtable]$Cfg)

    $iamToken = $Cfg["IAM_TOKEN"]
    if ($iamToken) {
        return "Bearer $($iamToken.Trim())"
    }

    Require-Command -Name yc
    $tokenOutput = (& yc iam create-token 2>&1 | Out-String)
    if ($LASTEXITCODE -eq 0 -and $tokenOutput) {
        $createdToken = $null
        foreach ($line in ($tokenOutput -split "`r?`n")) {
            $trimmed = $line.Trim()
            if ($trimmed -match '^t1\.[A-Za-z0-9._-]+$') {
                $createdToken = $trimmed
                break
            }
        }

        if ($createdToken) {
            return "Bearer $createdToken"
        }
    }

    $apiKey = $Cfg["API_KEY"]
    if ($apiKey) {
        return "Api-Key $($apiKey.Trim())"
    }

    throw "Failed to obtain IAM token via 'yc iam create-token'. Output: $tokenOutput"
}

function ConvertTo-CompactJson {
    param([object]$Value)
    return ($Value | ConvertTo-Json -Depth 100 -Compress)
}

function Build-QueryString {
    param([hashtable]$Query)
    if (-not $Query -or $Query.Count -eq 0) { return "" }
    $pairs = @()
    foreach ($entry in $Query.GetEnumerator()) {
        $k = [uri]::EscapeDataString([string]$entry.Key)
        $v = [uri]::EscapeDataString([string]$entry.Value)
        $pairs += "$k=$v"
    }
    return [string]::Join("&", $pairs)
}

function New-ApiException {
    param(
        [int]$Status,
        [string]$Body
    )
    $ex = [System.Exception]::new("HTTP ${Status}: $Body")
    $ex.Data["Status"] = $Status
    $ex.Data["Body"] = $Body
    return $ex
}

function Invoke-JsonRequest {
    param(
        [string]$Method,
        [string]$Url,
        [string]$AuthHeader,
        [int]$TimeoutSec,
        [hashtable]$Query = $null,
        [object]$Payload = $null
    )

    $finalUrl = $Url
    if ($Query) {
        $qs = Build-QueryString -Query $Query
        if ($qs) { $finalUrl = "$Url`?$qs" }
    }

    $headers = @{ Authorization = $AuthHeader }
    $bodyText = $null
    if ($null -ne $Payload) {
        $bodyText = $Payload | ConvertTo-Json -Depth 100
        $headers["Content-Type"] = "application/json"
    }

    try {
        $resp = Invoke-WebRequest -Method $Method -Uri $finalUrl -Headers $headers -Body $bodyText -TimeoutSec $TimeoutSec
        $content = $resp.Content
        if (-not $content -or -not $content.Trim()) { return @{} }
        $parsed = $content | ConvertFrom-Json -Depth 100
        if ($parsed -is [System.Collections.IDictionary]) { return $parsed }
        return @{ value = $parsed }
    }
    catch {
        $status = 0
        $body = ""
        if ($_.Exception.PSObject.Properties["Response"] -and $_.Exception.Response) {
            try {
                $status = [int]$_.Exception.Response.StatusCode
            }
            catch {
                $status = 0
            }
        }
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $body = [string]$_.ErrorDetails.Message
        }
        elseif ($_.Exception.Message) {
            $body = [string]$_.Exception.Message
        }
        throw (New-ApiException -Status $status -Body $body)
    }
}

function Resolve-PathSafe {
    param([string]$PathValue)
    return (Resolve-Path -LiteralPath $PathValue).Path
}

function Read-Spec {
    param([string]$PathValue)

    if (-not (Test-Path -LiteralPath $PathValue)) {
        throw "Spec file not found: $PathValue"
    }
    $raw = Get-Content -LiteralPath $PathValue -Raw
    $ext = [System.IO.Path]::GetExtension($PathValue).ToLowerInvariant()

    if ($ext -in @(".yaml", ".yml")) {
        if (Get-Command ConvertFrom-Yaml -ErrorAction SilentlyContinue) {
            $spec = $raw | ConvertFrom-Yaml
        }
        else {
            Require-Command -Name python
            $json = & python -c "import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1],'r',encoding='utf-8').read()) or {}))" $PathValue
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to parse YAML spec with Python: $PathValue"
            }
            $spec = $json | ConvertFrom-Json -Depth 100
        }
    }
    elseif ($ext -eq ".json") {
        $spec = $raw | ConvertFrom-Json -Depth 100
    }
    else {
        throw "Unsupported spec extension '$ext', expected .yaml/.yml/.json"
    }

    if ($null -eq $spec) {
        throw "Spec root must be an object: $PathValue"
    }
    return $spec
}

function Require-NonEmptyString {
    param([object]$Container, [string]$Key, [string]$Context)

    $value = $Container.$Key
    if (-not ($value -is [string]) -or -not $value.Trim()) {
        throw "$Context.$Key must be a non-empty string"
    }
    return $value.Trim()
}

function Validate-ToolSpec {
    param(
        [object]$Spec,
        [string]$Source
    )

    $tool = $Spec.tool
    if ($null -eq $tool) { throw "${Source}: spec.tool must be an object" }
    [void](Require-NonEmptyString -Container $tool -Key "name" -Context "tool")
    [void](Require-NonEmptyString -Container $tool -Key "description" -Context "tool")

    if ($null -eq $tool.input_json_schema) {
        throw "${Source}: tool.input_json_schema must be an object"
    }

    $functionCall = $tool.function_call
    if ($null -eq $functionCall) {
        throw "${Source}: tool.function_call must be an object"
    }
    [void](Require-NonEmptyString -Container $functionCall -Key "function_id" -Context "tool.function_call")
    [void](Require-NonEmptyString -Container $functionCall -Key "tag" -Context "tool.function_call")
}

function Load-AndValidateSpecs {
    param([string[]]$SpecPaths)

    $seen = @{}
    $loaded = @()
    foreach ($path in $SpecPaths) {
        $resolved = Resolve-PathSafe -PathValue $path
        $spec = Read-Spec -PathValue $resolved
        Validate-ToolSpec -Spec $spec -Source $resolved
        $toolName = [string]$spec.tool.name
        if ($seen.ContainsKey($toolName)) {
            throw "Duplicate tool.name across specs: $toolName"
        }
        $seen[$toolName] = $true
        $loaded += @{
            Path = $resolved
            Spec = $spec
        }
    }
    return $loaded
}

function Get-FieldName {
    param(
        [string]$FieldStyle,
        [string]$Camel,
        [string]$Snake
    )
    if ($FieldStyle -eq "camel") { return $Camel }
    return $Snake
}

function Build-ToolPayload {
    param(
        [string]$FieldStyle,
        [object]$Tool
    )

    $actionCallField = Get-FieldName -FieldStyle $FieldStyle -Camel "functionCall" -Snake "function_call"
    $functionIdField = Get-FieldName -FieldStyle $FieldStyle -Camel "functionId" -Snake "function_id"
    $schemaField = Get-FieldName -FieldStyle $FieldStyle -Camel "inputJsonSchema" -Snake "input_json_schema"

    $actionCall = @{
        tag = $Tool.function_call.tag
    }
    $actionCall[$functionIdField] = $Tool.function_call.function_id

    $action = @{}
    $action[$actionCallField] = $actionCall

    $payload = @{
        name = $Tool.name
        description = $Tool.description
        action = $action
    }
    $payload[$schemaField] = ConvertTo-CompactJson -Value $Tool.input_json_schema
    return $payload
}

function Build-CreatePayload {
    param(
        [string]$FieldStyle,
        [array]$LoadedSpecs,
        [string]$FolderId,
        [string]$ServiceAccountId,
        [string]$GatewayName,
        [string]$GatewayDescription,
        [bool]$IsPublic,
        [string]$CloudId
    )

    $folderField = Get-FieldName -FieldStyle $FieldStyle -Camel "folderId" -Snake "folder_id"
    $serviceField = Get-FieldName -FieldStyle $FieldStyle -Camel "serviceAccountId" -Snake "service_account_id"
    $logField = Get-FieldName -FieldStyle $FieldStyle -Camel "logOptions" -Snake "log_options"
    $minLevelField = Get-FieldName -FieldStyle $FieldStyle -Camel "minLevel" -Snake "min_level"
    $cloudField = Get-FieldName -FieldStyle $FieldStyle -Camel "cloudId" -Snake "cloud_id"

    $tools = @()
    foreach ($entry in $LoadedSpecs) {
        $tools += Build-ToolPayload -FieldStyle $FieldStyle -Tool $entry.Spec.tool
    }

    $logOptions = @{
        $folderField = $FolderId
        $minLevelField = "INFO"
    }

    $payload = @{
        name = $GatewayName
        description = $GatewayDescription
        public = $IsPublic
        tools = $tools
        $folderField = $FolderId
        $serviceField = $ServiceAccountId
        $logField = $logOptions
    }
    if ($CloudId) { $payload[$cloudField] = $CloudId }
    return $payload
}

function Extract-Items {
    param([object]$Response)

    if ($null -eq $Response) { return @() }
    foreach ($key in @("mcpGateways", "mcp_gateways", "items")) {
        $prop = $Response.PSObject.Properties[$key]
        if ($null -eq $prop) { continue }
        $value = $prop.Value
        if ($value -is [System.Collections.IEnumerable] -and -not ($value -is [string])) {
            return @($value)
        }
    }
    return @()
}

function Extract-NextToken {
    param([object]$Response)

    if ($null -eq $Response) { return $null }
    foreach ($key in @("nextPageToken", "next_page_token")) {
        $prop = $Response.PSObject.Properties[$key]
        if ($null -eq $prop) { continue }
        $value = $prop.Value
        if ($value -is [string] -and $value) { return $value }
    }
    return $null
}

function List-Gateways {
    param(
        [string]$McpApiBaseUrl,
        [string]$FolderId,
        [string]$AuthHeader,
        [int]$TimeoutSec
    )

    $allItems = @()
    $token = $null
    while ($true) {
        $query = @{ folderId = $FolderId }
        if ($token) { $query["pageToken"] = $token }
        try {
            $resp = Invoke-JsonRequest -Method "GET" -Url "$McpApiBaseUrl/mcpGateways" -AuthHeader $AuthHeader -TimeoutSec $TimeoutSec -Query $query
        }
        catch {
            $status = [int]$_.Exception.Data["Status"]
            $body = [string]$_.Exception.Data["Body"]
            if ($status -ge 400 -and $body -match "folderId" -and $body.ToLowerInvariant() -match "unknown") {
                $query = @{ folder_id = $FolderId }
                if ($token) { $query["page_token"] = $token }
                $resp = Invoke-JsonRequest -Method "GET" -Url "$McpApiBaseUrl/mcpGateways" -AuthHeader $AuthHeader -TimeoutSec $TimeoutSec -Query $query
            }
            else {
                throw
            }
        }
        $items = Extract-Items -Response $resp
        $allItems += $items
        $token = Extract-NextToken -Response $resp
        if (-not $token) { break }
    }
    return $allItems
}

function Is-OperationObject {
    param([object]$Value)
    if ($null -eq $Value) { return $false }
    return (($null -ne $Value.PSObject.Properties["done"]) -or ($null -ne $Value.PSObject.Properties["error"]) -or ($null -ne $Value.PSObject.Properties["metadata"]))
}

function Wait-Operation {
    param(
        [object]$OperationObj,
        [string]$OperationApiBaseUrl,
        [string]$AuthHeader,
        [int]$TimeoutSec,
        [int]$PollIntervalSec = 2
    )

    if (-not (Is-OperationObject -Value $OperationObj)) { return $OperationObj }
    $doneProp = $OperationObj.PSObject.Properties["done"]
    if ($doneProp -and $doneProp.Value -eq $true) { return $OperationObj }

    $operationIdProp = $OperationObj.PSObject.Properties["id"]
    $operationId = if ($operationIdProp) { [string]$operationIdProp.Value } else { "" }
    if (-not $operationId) { return $OperationObj }

    $deadline = (Get-Date).ToUniversalTime().AddSeconds($TimeoutSec)
    while ((Get-Date).ToUniversalTime() -lt $deadline) {
        $current = Invoke-JsonRequest -Method "GET" -Url "$OperationApiBaseUrl/$operationId" -AuthHeader $AuthHeader -TimeoutSec $TimeoutSec
        $currentDoneProp = $current.PSObject.Properties["done"]
        if ($currentDoneProp -and $currentDoneProp.Value -eq $true) { return $current }
        Start-Sleep -Seconds $PollIntervalSec
    }
    throw "Operation did not complete in $TimeoutSec sec: $operationId"
}

function Get-ResponseErrorText {
    param([object]$Obj)
    if ($null -eq $Obj) { return $null }
    $errProp = $Obj.PSObject.Properties["error"]
    if (-not $errProp) { return $null }
    $err = $errProp.Value
    if ($err -is [string] -and $err) { return $err }
    if ($err -and $err.code -or $err.message) {
        $code = [string]$err.code
        $message = [string]$err.message
        $text = "${code}: $message".Trim(": ")
        return $text
    }
    return $null
}

function Get-PropValue {
    param(
        [object]$Obj,
        [string]$Name
    )
    if ($null -eq $Obj) { return $null }
    $prop = $Obj.PSObject.Properties[$Name]
    if ($null -eq $prop) { return $null }
    return $prop.Value
}

function Main {
    param([string[]]$RawArgs)

    try {
        $argsParsed = Parse-Args -RawArgs $RawArgs
    }
    catch {
        Write-Error $_.Exception.Message
        Show-Usage
        exit 2
    }

    $envFileResolved = Resolve-Path -LiteralPath $argsParsed.EnvFile -ErrorAction SilentlyContinue
    $envFilePath = if ($envFileResolved) { $envFileResolved.Path } else { [System.IO.Path]::GetFullPath($argsParsed.EnvFile) }
    $dotEnvValues = Read-DotEnv -Path $envFilePath
    $cfg = Build-Config -DotEnvValues $dotEnvValues

    $missing = @()
    foreach ($requiredKey in @("FOLDER_ID", "SERVICE_ACCOUNT_ID")) {
        if (-not $cfg[$requiredKey]) { $missing += $requiredKey }
    }
    if ($missing.Count -gt 0) {
        Write-Error ("Missing required config values: " + ($missing -join ", "))
        Write-Error "Checked env file: $envFilePath"
        exit 2
    }

    try {
        $authHeader = Get-AuthHeader -Cfg $cfg
    }
    catch {
        Write-Error $_.Exception.Message
        exit 2
    }

    try {
        $loadedSpecs = @(Load-AndValidateSpecs -SpecPaths $argsParsed.Spec)
    }
    catch {
        Write-Error ("Spec validation error: " + $_.Exception.Message)
        exit 2
    }

    $timeoutSec = 30
    if ($cfg["REQUEST_TIMEOUT_SEC"]) {
        [void][int]::TryParse([string]$cfg["REQUEST_TIMEOUT_SEC"], [ref]$timeoutSec)
    }

    $mcpApiBaseUrl = ([string]($cfg["MCP_API_BASE_URL"] ?? $DEFAULT_MCP_API_BASE_URL)).TrimEnd("/")
    $operationApiBaseUrl = ([string]($cfg["OPERATION_API_BASE_URL"] ?? $DEFAULT_OPERATION_API_BASE_URL)).TrimEnd("/")
    $folderId = [string]$cfg["FOLDER_ID"]
    $serviceAccountId = [string]$cfg["SERVICE_ACCOUNT_ID"]
    $cloudId = [string]$cfg["CLOUD_ID"]
    $isPublic = To-Bool -Value ([string]$cfg["MCP_PUBLIC"]) -Default $true

    $candidates = @(
        @{
            FieldStyle = "camel"
            Payload = (Build-CreatePayload -FieldStyle "camel" -LoadedSpecs $loadedSpecs -FolderId $folderId -ServiceAccountId $serviceAccountId -GatewayName $argsParsed.GatewayName -GatewayDescription $argsParsed.GatewayDescription -IsPublic $isPublic -CloudId $cloudId)
        },
        @{
            FieldStyle = "snake"
            Payload = (Build-CreatePayload -FieldStyle "snake" -LoadedSpecs $loadedSpecs -FolderId $folderId -ServiceAccountId $serviceAccountId -GatewayName $argsParsed.GatewayName -GatewayDescription $argsParsed.GatewayDescription -IsPublic $isPublic -CloudId $cloudId)
        }
    )

    if ($argsParsed.DryRun) {
        $first = $candidates[0]
        Write-Host "Dry run mode: first payload candidate (field_style=$($first.FieldStyle))"
        $first.Payload | ConvertTo-Json -Depth 100
        exit 0
    }

    try {
        $gateways = List-Gateways -McpApiBaseUrl $mcpApiBaseUrl -FolderId $folderId -AuthHeader $authHeader -TimeoutSec $timeoutSec
        $toDelete = @($gateways | Where-Object { $_.name -eq $argsParsed.GatewayName })
        if ($toDelete.Count -gt 0) {
            Write-Host "Found $($toDelete.Count) existing MCP server(s) named '$($argsParsed.GatewayName)', deleting..."
        }
        foreach ($g in $toDelete) {
            $gatewayId = [string]$g.id
            if (-not $gatewayId) { continue }
            $deleteResp = Invoke-JsonRequest -Method "DELETE" -Url "$mcpApiBaseUrl/mcpGateways/$gatewayId" -AuthHeader $authHeader -TimeoutSec $timeoutSec
            $deleteDone = Wait-Operation -OperationObj $deleteResp -OperationApiBaseUrl $operationApiBaseUrl -AuthHeader $authHeader -TimeoutSec 180
            $deleteErr = Get-ResponseErrorText -Obj $deleteDone
            if ($deleteErr) {
                throw "Failed to delete MCP server ${gatewayId}: $deleteErr"
            }
        }

        $createDone = $null
        $chosenFieldStyle = $null
        $lastError = $null

        foreach ($candidate in $candidates) {
            try {
                if ($argsParsed.Verbose) {
                    Write-Host "Trying create payload: field_style=$($candidate.FieldStyle)"
                }
                $createResp = Invoke-JsonRequest -Method "POST" -Url "$mcpApiBaseUrl/mcpGateways" -AuthHeader $authHeader -TimeoutSec $timeoutSec -Payload $candidate.Payload
                $createDone = Wait-Operation -OperationObj $createResp -OperationApiBaseUrl $operationApiBaseUrl -AuthHeader $authHeader -TimeoutSec 180
                $createErr = Get-ResponseErrorText -Obj $createDone
                if ($createErr) { throw $createErr }
                $chosenFieldStyle = [string]$candidate.FieldStyle
                break
            }
            catch {
                $lastError = $_.Exception.Message
                if ($argsParsed.Verbose) {
                    Write-Host "Create attempt failed ($($candidate.FieldStyle)): $lastError"
                }
            }
        }

        if ($null -eq $createDone -or -not $chosenFieldStyle) {
            throw "All create payload variants failed. Last error: $lastError"
        }

        $createdResource = Get-PropValue -Obj $createDone -Name "response"
        if ($null -eq $createdResource) {
            $gatewaysAfter = List-Gateways -McpApiBaseUrl $mcpApiBaseUrl -FolderId $folderId -AuthHeader $authHeader -TimeoutSec $timeoutSec
            $matched = @($gatewaysAfter | Where-Object { $_.name -eq $argsParsed.GatewayName })
            if ($matched.Count -gt 0) {
                $createdResource = $matched[$matched.Count - 1]
            }
            else {
                $createdResource = @{}
            }
        }

        $createdId = [string](Get-PropValue -Obj $createdResource -Name "id")
        if (-not $createdId) { $createdId = "<unknown>" }

        $baseDomain = [string](Get-PropValue -Obj $createdResource -Name "base_domain")
        if (-not $baseDomain) {
            $baseDomain = [string](Get-PropValue -Obj $createdResource -Name "baseDomain")
        }
        if (-not $baseDomain) { $baseDomain = "<unknown>" }

        $status = [string](Get-PropValue -Obj $createdResource -Name "status")
        if (-not $status) { $status = "<unknown>" }

        Write-Host "MCP server created successfully."
        Write-Host "id: $createdId"
        Write-Host "name: $($argsParsed.GatewayName)"
        Write-Host "status: $status"
        Write-Host "base_domain: $baseDomain"
        Write-Host "payload_variant: field_style=$chosenFieldStyle"

        $firstSpecPath = [string]$loadedSpecs[0]["Path"]
        $specDir = Split-Path -Parent $firstSpecPath
        $outPath = Join-Path $specDir "$($argsParsed.GatewayName).created.json"
        ($createdResource | ConvertTo-Json -Depth 100) | Set-Content -LiteralPath $outPath -Encoding UTF8
        Write-Host "saved: $outPath"
        exit 0
    }
    catch {
        $status = $_.Exception.Data["Status"]
        if ($status) {
            Write-Error ("API error: " + $_.Exception.Message)
        }
        else {
            Write-Error ("Error: " + $_.Exception.Message)
        }
        exit 1
    }
}

Main -RawArgs $args
