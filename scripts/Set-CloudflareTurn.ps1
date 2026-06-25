param(
    [Parameter(Mandatory = $true)]
    [string]$KeyId,

    [Parameter(Mandatory = $true)]
    [string]$ApiToken,

    [int]$TtlSeconds = 600,

    [switch]$DisableForceRelay
)

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$secretsPath = Join-Path $projectRoot "secrets.local.ps1"

if (-not (Test-Path -LiteralPath $secretsPath)) {
    & (Join-Path $PSScriptRoot "New-LocalSecrets.ps1")
}

function Set-EnvLine {
    param(
        [string[]]$Lines,
        [string]$Name,
        [string]$Value
    )

    $escapedValue = $Value.Replace("``", "````").Replace('"', '`"')
    $line = '$env:' + $Name + ' = "' + $escapedValue + '"'
    $pattern = '^\$env:' + [regex]::Escape($Name) + '\s*='

    $found = $false
    $updated = foreach ($existingLine in $Lines) {
        if ($existingLine -match $pattern) {
            $found = $true
            $line
        } else {
            $existingLine
        }
    }

    if (-not $found) {
        $updated += $line
    }

    $updated
}

$lines = Get-Content -LiteralPath $secretsPath
$lines = Set-EnvLine -Lines $lines -Name "CLOUDFLARE_TURN_KEY_ID" -Value $KeyId
$lines = Set-EnvLine -Lines $lines -Name "CLOUDFLARE_TURN_API_TOKEN" -Value $ApiToken
$lines = Set-EnvLine -Lines $lines -Name "CLOUDFLARE_TURN_TTL_SECONDS" -Value ([string]$TtlSeconds)
$lines = Set-EnvLine -Lines $lines -Name "CCTV_FORCE_TURN_RELAY" -Value ($(if ($DisableForceRelay) { "false" } else { "true" }))
Set-Content -LiteralPath $secretsPath -Value $lines

$env:CLOUDFLARE_TURN_KEY_ID = $KeyId
$env:CLOUDFLARE_TURN_API_TOKEN = $ApiToken
$env:CLOUDFLARE_TURN_TTL_SECONDS = [string]$TtlSeconds

& (Join-Path $PSScriptRoot "Test-CloudflareTurn.ps1")
