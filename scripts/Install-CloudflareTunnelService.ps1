param(
    [Parameter(Mandatory = $true)]
    [string]$TunnelToken
)

$cloudflared = & (Join-Path $PSScriptRoot "Resolve-Cloudflared.ps1")
& $cloudflared service install $TunnelToken

Write-Host "Installed the Cloudflare Tunnel Windows service."
Write-Host "Check status with: Get-Service cloudflared"
