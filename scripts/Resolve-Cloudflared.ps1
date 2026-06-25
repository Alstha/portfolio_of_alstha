$cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($cmd) {
    return $cmd.Source
}

$candidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "cloudflared\cloudflared.exe"),
    (Join-Path $env:ProgramFiles "cloudflared\cloudflared.exe"),
    (Join-Path $env:LOCALAPPDATA "cloudflared\cloudflared.exe")
)

foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
        return $candidate
    }
}

throw "cloudflared.exe was not found. Install it with: winget install --id Cloudflare.cloudflared --exact"
