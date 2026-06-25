param(
    [switch]$Force,
    [string]$Username = "parent",
    [string]$AllowedHosts = ""
)

$secretsDir = Join-Path $env:LOCALAPPDATA "FamilyCCTV\secrets"
if (-not (Test-Path -LiteralPath $secretsDir)) {
    New-Item -Path $secretsDir -ItemType Directory -Force | Out-Null
}
$secretsPath = Join-Path $secretsDir "secrets.local.ps1"

if ((Test-Path -LiteralPath $secretsPath) -and -not $Force) {
    Write-Host "Secrets already exist: $secretsPath"
    return
}

function New-Secret([int]$ByteCount) {
    $bytes = [byte[]]::new($ByteCount)
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

$password = New-Secret 24
$sessionSecret = New-Secret 32

$content = @"
`$env:CCTV_USERNAME = "$Username"
`$env:CCTV_PASSWORD = "$password"
`$env:CCTV_SESSION_SECRET = "$sessionSecret"
`$env:CCTV_CAMERA_INDEX = "0"
`$env:CCTV_FRAME_WIDTH = "640"
`$env:CCTV_FRAME_HEIGHT = "480"
`$env:CCTV_FPS = "12"
`$env:CCTV_JPEG_QUALITY = "70"
`$env:CCTV_LOW_LIGHT_ENHANCE = "false"
`$env:CCTV_LOW_LIGHT_THRESHOLD = "90"
`$env:CCTV_LOW_LIGHT_TARGET = "125"
`$env:CCTV_LOW_LIGHT_MAX_GAIN = "3.0"
`$env:CCTV_LOW_LIGHT_BETA = "8"
`$env:CCTV_TRUST_PROXY_HTTPS = "false"
`$env:CCTV_COOKIE_SECURE = "false"
`$env:CCTV_HSTS = "false"
`$env:CCTV_ALLOWED_HOSTS = "$AllowedHosts"
`$env:CCTV_REQUIRE_CLOUDFLARE_ACCESS = "false"
`$env:CCTV_CLOUDFLARE_ACCESS_HOSTS = ""
`$env:CCTV_CLOUDFLARE_ACCESS_EMAILS = ""
`$env:CCTV_ENABLE_STUN = "true"
`$env:CCTV_STUN_SERVERS = "stun:stun.cloudflare.com:3478,stun:stun.l.google.com:19302"
`$env:CCTV_LIVE_IDLE_STOP_SECONDS = "180"
`$env:CCTV_WEBRTC_HEARTBEAT_SECONDS = "10"
`$env:CCTV_WEBRTC_PEER_TIMEOUT_SECONDS = "45"

# Fill these after creating Cloudflare Realtime TURN credentials.
`$env:CLOUDFLARE_TURN_KEY_ID = ""
`$env:CLOUDFLARE_TURN_API_TOKEN = ""
`$env:CLOUDFLARE_TURN_TTL_SECONDS = "600"
`$env:CCTV_FORCE_TURN_RELAY = "true"
`$env:CCTV_SCREENSHOT_MONITOR = "1"
`$env:CCTV_BELL_AUDIO = ""
`$env:CCTV_BELL_COOLDOWN_SECONDS = "3"
`$env:CCTV_BELL_PLAY_MILLISECONDS = "12000"
`$env:CCTV_MESSAGE_VIDEO_MAX_MB = "25"
`$env:CCTV_MESSAGE_VIDEO_MAX_SECONDS = "30"
`$env:CCTV_MESSAGE_VIDEO_DIR = "received_messages"
`$env:CCTV_MESSAGE_AUTOPLAY = "true"
`$env:CCTV_MESSAGE_PLAYER_BROWSER = ""
`$env:CCTV_PASSKEYS_ENABLED = "true"
`$env:CCTV_PASSKEY_STORE = "`$env:LOCALAPPDATA\FamilyCCTV\secrets\passkeys.local.json"
"@

Set-Content -LiteralPath $secretsPath -Value $content -Encoding UTF8
Write-Host "Created $secretsPath"
Write-Host "Username: $Username"
Write-Host "Password: $password"
