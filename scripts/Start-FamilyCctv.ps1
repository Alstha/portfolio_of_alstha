param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000
)

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$secretsDir = Join-Path $env:LOCALAPPDATA "FamilyCCTV\secrets"
$secretsPath = Join-Path $secretsDir "secrets.local.ps1"

if (-not (Test-Path -LiteralPath $secretsPath)) {
    & (Join-Path $PSScriptRoot "New-LocalSecrets.ps1")
}

. $secretsPath
Set-Location $projectRoot

python -u -m uvicorn app:app --host $HostAddress --port $Port --proxy-headers --forwarded-allow-ips="127.0.0.1"
