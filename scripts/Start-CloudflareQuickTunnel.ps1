param(
    [int]$Port = 8000,
    [string]$LogFile = "cloudflared-quick.log"
)

$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$cloudflared = & (Join-Path $PSScriptRoot "Resolve-Cloudflared.ps1")
$target = "http://127.0.0.1:$Port"
$logPath = Join-Path $projectRoot $LogFile

Write-Host "Starting Cloudflare Quick Tunnel to $target"
Write-Host "Log: $logPath"
& $cloudflared tunnel --url $target --logfile $logPath
