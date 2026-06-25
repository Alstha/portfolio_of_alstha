param(
    [string]$LogFile = "cloudflared-quick.log"
)

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$logPath = Join-Path $projectRoot $LogFile

if (-not (Test-Path -LiteralPath $logPath)) {
    throw "Quick tunnel log not found: $logPath"
}

$text = Get-Content -Raw -LiteralPath $logPath
$matches = [regex]::Matches($text, "https://[a-z0-9-]+\.trycloudflare\.com")
if ($matches.Count -eq 0) {
    throw "No trycloudflare.com URL found in $logPath"
}

$matches[$matches.Count - 1].Value
