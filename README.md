# Family CCTV Project

Private, consent-based webcam streaming for a laptop webcam. The camera is not opened when the server starts. It opens only while an authenticated viewer starts the live WebRTC stream, then releases when the viewer stops or leaves.

## What this does

- Password-protected FastAPI web app.
- Signed session cookie instead of a plain `auth=ok` cookie.
- Login throttling to slow down password guessing.
- 480p 4:3 defaults for a lightweight USB webcam.
- Manual-start WebRTC live video for lower latency than MJPEG.
- Zoom controls for the live video, with drag-to-pan while zoomed.
- Cloudflare TURN support for production WebRTC connectivity.
- MJPEG endpoint at `/video` for authenticated local debugging only. The viewer does not use it as a fallback.
- One-shot authenticated desktop screenshot button.
- Authenticated laptop bell button with 12 second playback.
- Auto-stop for forgotten live sessions, plus a server-side WebRTC heartbeat timeout.
- Authenticated video messages from the viewer browser to the laptop, with capped size and duration.
- No CCTV recording and no cloud footage upload by default.

Use this only where everyone involved consents and local law allows it.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Configure

Set a private username, password, and session secret before running:

```powershell
$env:CCTV_USERNAME = "parent"
$env:CCTV_PASSWORD = "use-a-long-password-here"
$env:CCTV_SESSION_SECRET = (python -c "import secrets; print(secrets.token_urlsafe(32))")
```

Optional low-resource camera settings:

```powershell
$env:CCTV_CAMERA_INDEX = "0"
$env:CCTV_FRAME_WIDTH = "640"
$env:CCTV_FRAME_HEIGHT = "480"
$env:CCTV_FPS = "12"
$env:CCTV_JPEG_QUALITY = "70"
```

Optional desktop screenshot and bell settings:

```powershell
$env:CCTV_SCREENSHOT_MONITOR = "1"
$env:CCTV_BELL_AUDIO = "C:\Path\To\bell.mp3"
$env:CCTV_BELL_COOLDOWN_SECONDS = "3"
$env:CCTV_BELL_PLAY_MILLISECONDS = "12000"
```

If `CCTV_BELL_AUDIO` is empty, Windows uses a built-in beep pattern for the configured duration.

Optional live-session and video message settings:

```powershell
$env:CCTV_ENABLE_STUN = "true"
$env:CCTV_STUN_SERVERS = "stun:stun.cloudflare.com:3478,stun:stun.l.google.com:19302"
$env:CCTV_LIVE_IDLE_STOP_SECONDS = "180"
$env:CCTV_WEBRTC_HEARTBEAT_SECONDS = "10"
$env:CCTV_WEBRTC_PEER_TIMEOUT_SECONDS = "45"
$env:CCTV_MESSAGE_VIDEO_MAX_MB = "25"
$env:CCTV_MESSAGE_VIDEO_MAX_SECONDS = "30"
$env:CCTV_MESSAGE_VIDEO_DIR = "received_messages"
$env:CCTV_MESSAGE_AUTOPLAY = "true"
$env:CCTV_MESSAGE_PLAYER_BROWSER = ""
```

## Run Locally

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

This project also includes Windows helper scripts:

```powershell
# Generate local password/session secrets if missing.
powershell -ExecutionPolicy Bypass -File .\scripts\New-LocalSecrets.ps1

# Start the camera app using secrets.local.ps1.
powershell -ExecutionPolicy Bypass -File .\scripts\Start-FamilyCctv.ps1

# Start a temporary Cloudflare Quick Tunnel.
powershell -ExecutionPolicy Bypass -File .\scripts\Start-CloudflareQuickTunnel.ps1

# Print the current Quick Tunnel URL.
powershell -ExecutionPolicy Bypass -File .\scripts\Get-QuickTunnelUrl.ps1

# Stop the local app and tunnel.
powershell -ExecutionPolicy Bypass -File .\scripts\Stop-FamilyCctv.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\Stop-CloudflareTunnel.ps1
```

## Production With Cloudflare

Use Cloudflare in two parts:

- Cloudflare Tunnel publishes this app over HTTPS without opening a router port.
- Cloudflare Realtime TURN gives WebRTC a relay path when direct peer networking fails.
- Cloudflare Access should restrict the public hostname to your parents' email accounts before they even reach this app's login page.

This app is configured as WebRTC-only in the viewer. Cloudflare Tunnel publishes the web app, but it is not enough by itself for reliable WebRTC from another city. The ICE path is chosen in this order:

- Cloudflare TURN, if configured and ready.
- STUN, if `CCTV_ENABLE_STUN=true` and `CCTV_STUN_SERVERS` is set.
- Plain WebRTC with no STUN/TURN ICE servers.

Plain WebRTC can still work on the same LAN because browsers gather local host candidates automatically. Without TURN credentials, the page uses direct/STUN WebRTC by default. That is lowest latency when it works, but it can fail on restrictive NAT/firewall networks.

STUN/direct mode most often fails on:

- Mobile data behind carrier-grade NAT.
- Office, school, hotel, and public Wi-Fi that blocks peer-to-peer UDP.
- Networks using symmetric NAT or strict firewalls.
- Some VPN/proxy networks.
- Router/firewall setups that block UDP media ports.

For those networks, configure Cloudflare Realtime TURN so the browser has a relay path. The app will still prefer direct/STUN when possible and use TURN only when needed.

Recommended production environment:

```powershell
$env:CCTV_USERNAME = "parent"
$env:CCTV_PASSWORD = "use-a-long-password-here"
$env:CCTV_SESSION_SECRET = (python -c "import secrets; print(secrets.token_urlsafe(32))")
$env:CCTV_TRUST_PROXY_HTTPS = "true"
$env:CCTV_COOKIE_SECURE = "true"
$env:CCTV_HSTS = "true"
$env:CCTV_ALLOWED_HOSTS = "camera.your-domain.com"
$env:CLOUDFLARE_TURN_KEY_ID = "your-turn-key-id"
$env:CLOUDFLARE_TURN_API_TOKEN = "your-cloudflare-turn-token"
$env:CLOUDFLARE_TURN_TTL_SECONDS = "600"
```

Run the app on the laptop:

```powershell
uvicorn app:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips="127.0.0.1"
```

In Cloudflare Tunnel, publish the local service:

```text
http://127.0.0.1:8000
```

If Cloudflare gives you a tunnel token, install it as a Windows service:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\Install-CloudflareTunnelService.ps1 -TunnelToken "PASTE_CLOUDFLARE_TUNNEL_TOKEN"
```

Then set the public hostname in Cloudflare to:

```text
camera.your-domain.com -> http://127.0.0.1:8000
```

Official docs:

- Cloudflare Tunnel setup: https://developers.cloudflare.com/tunnel/setup/
- Cloudflare Tunnel service routes: https://developers.cloudflare.com/cloudflare-one/networks/routes/add-routes/
- Cloudflare Access self-hosted apps: https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/
- Cloudflare Realtime TURN: https://developers.cloudflare.com/realtime/turn/
- Cloudflare TURN credential generation: https://developers.cloudflare.com/realtime/turn/generate-credentials/

## Notes

- The webcam does not open on login. Press `Start Live` to open the camera and begin WebRTC.
- The live video section stays hidden until `Start Live` is pressed.
- On public Cloudflare Tunnel URLs, direct/STUN mode is fastest when it works. Use Cloudflare Realtime TURN for restrictive networks where direct WebRTC fails.
- Use the zoom slider, plus/minus buttons, and drag-to-pan to inspect the live video.
- Press `Stop`, close the tab, switch away from the tab, or wait for the idle timer to release the webcam.
- If the viewer browser disappears without cleanup, the server heartbeat timeout closes the WebRTC peer and releases the webcam.
- `Desktop Screenshot` sends one current laptop desktop screenshot to the authenticated viewer.
- `Ring Laptop` plays `CCTV_BELL_AUDIO` on the laptop for 12 seconds, or a default beep if no audio path is set.
- `Video Message` records from the viewer's browser camera and microphone after browser permission, uploads the clip, and opens it fullscreen on the laptop.
- Video messages are stored under `CCTV_MESSAGE_VIDEO_DIR`; keep the max duration and max MB low for a lightweight laptop.
- If WebRTC cannot connect remotely in direct/STUN mode, use Cloudflare TURN credentials.
- If the camera does not open, check Windows camera privacy settings and try another `CCTV_CAMERA_INDEX`.
- Lower `CCTV_FPS` if the laptop is slow. `CCTV_JPEG_QUALITY` only affects the debug `/video` endpoint.
