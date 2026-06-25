$processes = Get-Process cloudflared -ErrorAction SilentlyContinue
if (-not $processes) {
    Write-Host "No cloudflared process is running."
    return
}

$processes | Stop-Process -Force
Write-Host "Stopped cloudflared."
