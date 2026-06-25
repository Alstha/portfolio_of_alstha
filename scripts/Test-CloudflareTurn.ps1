$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$secretsPath = Join-Path $projectRoot "secrets.local.ps1"

if (Test-Path -LiteralPath $secretsPath) {
    . $secretsPath
}

if (-not $env:CLOUDFLARE_TURN_KEY_ID -or -not $env:CLOUDFLARE_TURN_API_TOKEN) {
    throw "Cloudflare TURN is not configured. Run scripts\Set-CloudflareTurn.ps1 with your KeyId and ApiToken."
}

$ttl = if ($env:CLOUDFLARE_TURN_TTL_SECONDS) { [int]$env:CLOUDFLARE_TURN_TTL_SECONDS } else { 600 }
$uri = "https://rtc.live.cloudflare.com/v1/turn/keys/$($env:CLOUDFLARE_TURN_KEY_ID)/credentials/generate-ice-servers"
$body = @{ ttl = $ttl } | ConvertTo-Json -Compress

try {
    $response = Invoke-RestMethod `
        -Method Post `
        -Uri $uri `
        -Headers @{ Authorization = "Bearer $($env:CLOUDFLARE_TURN_API_TOKEN)" } `
        -ContentType "application/json" `
        -Body $body `
        -TimeoutSec 20
} catch {
    throw "Cloudflare TURN credential test failed: $($_.Exception.Message)"
}

$servers = @($response.iceServers)
$turnUrls = @(
    $servers |
        ForEach-Object { @($_.urls) } |
        Where-Object { $_ -is [string] -and ($_ -like "turn:*" -or $_ -like "turns:*") -and $_ -notlike "*:53*" }
)

if ($turnUrls.Count -eq 0) {
    throw "Cloudflare responded, but no usable browser TURN URLs were returned."
}

Write-Host "Cloudflare TURN is ready."
Write-Host "Usable TURN URLs: $($turnUrls.Count)"
Write-Host "Force relay: $env:CCTV_FORCE_TURN_RELAY"
