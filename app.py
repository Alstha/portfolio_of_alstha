import asyncio
import hashlib
import importlib
import json
import logging
import os
import shutil
import subprocess
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque
from urllib import error as urlerror
from urllib import request as urlrequest
from dotenv import load_dotenv
load_dotenv()
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from starlette.background import BackgroundTask
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field


class SpeakPayload(BaseModel):
    text: str = Field(..., min_length=1, max_length=500)


class BroadcastPayload(BaseModel):
    text: str = Field(..., min_length=1, max_length=200)


class WebRTCOfferPayload(BaseModel):
    sdp: str = Field(..., min_length=10)
    type: str = Field(...)


class WebRTCPeerPayload(BaseModel):
    peerId: str = Field(..., min_length=1)


from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.sessions import SessionMiddleware
try:
    from webauthn import generate_authentication_options, generate_registration_options, options_to_json, verify_authentication_response, verify_registration_response
    from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
    from webauthn.helpers.structs import AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor, PublicKeyCredentialHint, ResidentKeyRequirement, UserVerificationRequirement
    WEBAUTHN_AVAILABLE = True
except Exception:
    WEBAUTHN_AVAILABLE = False
APP_NAME = 'परिवार सुरक्षा'
AUTH_SESSION_KEY = 'authenticated'
PASSKEY_REGISTRATION_CHALLENGE_KEY = 'passkey_registration_challenge'
PASSKEY_AUTHENTICATION_CHALLENGE_KEY = 'passkey_authentication_challenge'
DEFAULT_USERNAME = 'parent'
DEFAULT_FRAME_WIDTH = 640
DEFAULT_FRAME_HEIGHT = 480
DEFAULT_FPS = 12
DEFAULT_JPEG_QUALITY = 70
LOGIN_WINDOW_SECONDS = 300
MAX_LOGIN_ATTEMPTS = 8
VIDEO_MESSAGE_CONTENT_TYPES = {'video/webm': 'webm', 'audio/webm': 'webm',
    'video/mp4': 'mp4', 'application/mp4': 'mp4', 'video/quicktime': 'mov',
    'video/x-matroska': 'mkv', 'video/matroska': 'mkv'}
CLOUDFLARE_TURN_ENDPOINT_TEMPLATE = (
    'https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers'
    )
DEFAULT_STUN_SERVERS = ('stun:stun.cloudflare.com:3478',
    'stun:stun.l.google.com:19302')
logger = logging.getLogger('family_cctv')
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()


    class JSONFormatter(logging.Formatter):

        def format(self, record):
            log_record = {'time': self.formatTime(record, self.datefmt),
                'level': record.levelname, 'message': record.getMessage(),
                'logger': record.name}
            if record.exc_info:
                log_record['exc_info'] = self.formatException(record.exc_info)
            return json.dumps(log_record)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    logger.propagate = False
startup_time = time.time()
_heavy_import_lock = threading.Lock()
_cv2_module: Any | None = None
_numpy_module: Any | None = None
_mss_modules: tuple[Any, Any] | None = None
_aiortc_classes: tuple[Any, Any, Any, Any, Any] | None = None
_video_frame_class: Any | None = None


def get_cv2() ->Any:
    global _cv2_module
    if _cv2_module is None:
        with _heavy_import_lock:
            if _cv2_module is None:
                _cv2_module = importlib.import_module('cv2')
                try:
                    _cv2_module.setNumThreads(1)
                except Exception:
                    pass
    return _cv2_module


def get_numpy() ->Any:
    global _numpy_module
    if _numpy_module is None:
        with _heavy_import_lock:
            if _numpy_module is None:
                _numpy_module = importlib.import_module('numpy')
    return _numpy_module


def get_mss_modules() ->tuple[Any, Any]:
    global _mss_modules
    if _mss_modules is None:
        with _heavy_import_lock:
            if _mss_modules is None:
                mss_module = importlib.import_module('mss')
                mss_tools = importlib.import_module('mss.tools')
                _mss_modules = mss_module, mss_tools
    return _mss_modules


def get_aiortc_classes() ->tuple[Any, Any, Any, Any, Any]:
    global _aiortc_classes
    if _aiortc_classes is None:
        with _heavy_import_lock:
            if _aiortc_classes is None:
                aiortc = importlib.import_module('aiortc')
                _aiortc_classes = (aiortc.RTCConfiguration, aiortc.
                    RTCIceServer, aiortc.RTCPeerConnection, aiortc.
                    RTCSessionDescription, aiortc.VideoStreamTrack)
    return _aiortc_classes


def get_video_frame_class() ->Any:
    global _video_frame_class
    if _video_frame_class is None:
        with _heavy_import_lock:
            if _video_frame_class is None:
                _video_frame_class = importlib.import_module('av').VideoFrame
    return _video_frame_class


def env_int(name: str, default: int, minimum: int, maximum: int) ->int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def env_float(name: str, default: float, minimum: float, maximum: float
    ) ->float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def env_bool(name: str, default: bool=False) ->bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {'1', 'true', 'yes', 'on'}


def env_list(name: str, default: tuple[str, ...]=()) ->tuple[str, ...]:
    raw_value = os.getenv(name)
    if not raw_value:
        return default
    values = tuple(value.strip() for value in raw_value.split(',') if value
        .strip())
    return values or default


def video_extension(content_type: str, filename: (str | None)) ->(str | None):
    extension = VIDEO_MESSAGE_CONTENT_TYPES.get(content_type)
    if extension is not None:
        return extension
    suffix = Path(filename or '').suffix.lower().lstrip('.')
    if suffix == 'webm':
        return 'webm'
    if suffix in {'mp4', 'm4v'}:
        return 'mp4'
    if suffix == 'mov':
        return 'mov'
    if suffix == 'mkv':
        return 'mkv'
    return None


@dataclass(frozen=True)
class Settings:
    username: str = os.getenv('CCTV_USERNAME', DEFAULT_USERNAME)
    password: str | None = os.getenv('CCTV_PASSWORD')
    session_secret: str | None = os.getenv('CCTV_SESSION_SECRET')
    allowed_hosts: tuple[str, ...] = env_list('CCTV_ALLOWED_HOSTS')
    camera_index: int = env_int('CCTV_CAMERA_INDEX', 0, 0, 16)
    frame_width: int = env_int('CCTV_FRAME_WIDTH', DEFAULT_FRAME_WIDTH, 160,
        1920)
    frame_height: int = env_int('CCTV_FRAME_HEIGHT', DEFAULT_FRAME_HEIGHT, 
        120, 1080)
    fps: int = env_int('CCTV_FPS', DEFAULT_FPS, 1, 30)
    jpeg_quality: int = env_int('CCTV_JPEG_QUALITY', DEFAULT_JPEG_QUALITY, 
        30, 95)
    low_light_enhance: bool = env_bool('CCTV_LOW_LIGHT_ENHANCE', False)
    low_light_threshold: int = env_int('CCTV_LOW_LIGHT_THRESHOLD', 85, 1, 254)
    low_light_target: int = env_int('CCTV_LOW_LIGHT_TARGET', 120, 20, 255)
    low_light_max_gain: float = env_float('CCTV_LOW_LIGHT_MAX_GAIN', 2.8, 
        1.0, 6.0)
    low_light_beta: int = env_int('CCTV_LOW_LIGHT_BETA', 6, 0, 80)
    trust_proxy_https: bool = env_bool('CCTV_TRUST_PROXY_HTTPS', False)
    cookie_secure: bool = env_bool('CCTV_COOKIE_SECURE', env_bool(
        'CCTV_TRUST_PROXY_HTTPS', False))
    hsts: bool = env_bool('CCTV_HSTS', False)
    require_cloudflare_access: bool = env_bool('CCTV_REQUIRE_CLOUDFLARE_ACCESS'
        , False)
    cloudflare_access_emails: tuple[str, ...] = env_list(
        'CCTV_CLOUDFLARE_ACCESS_EMAILS')
    cloudflare_access_hosts: tuple[str, ...] = env_list(
        'CCTV_CLOUDFLARE_ACCESS_HOSTS')
    cloudflare_turn_key_id: str | None = os.getenv('CLOUDFLARE_TURN_KEY_ID')
    cloudflare_turn_api_token: str | None = os.getenv(
        'CLOUDFLARE_TURN_API_TOKEN')
    cloudflare_turn_ttl_seconds: int = env_int('CLOUDFLARE_TURN_TTL_SECONDS',
        600, 60, 86400)
    force_turn_relay: bool = env_bool('CCTV_FORCE_TURN_RELAY', False)
    ice_cache_seconds: int = env_int('CCTV_ICE_CACHE_SECONDS', 300, 0, 3600)
    enable_stun: bool = env_bool('CCTV_ENABLE_STUN', True)
    stun_servers: tuple[str, ...] = env_list('CCTV_STUN_SERVERS',
        DEFAULT_STUN_SERVERS)
    live_idle_stop_seconds: int = env_int('CCTV_LIVE_IDLE_STOP_SECONDS', 
        180, 30, 3600)
    webrtc_heartbeat_seconds: int = env_int('CCTV_WEBRTC_HEARTBEAT_SECONDS',
        10, 5, 60)
    webrtc_peer_timeout_seconds: int = env_int(
        'CCTV_WEBRTC_PEER_TIMEOUT_SECONDS', 45, 15, 600)
    screenshot_monitor: int = env_int('CCTV_SCREENSHOT_MONITOR', 1, 0, 8)
    bell_audio_path: str | None = os.getenv('CCTV_BELL_AUDIO')
    bell_cooldown_seconds: float = env_float('CCTV_BELL_COOLDOWN_SECONDS', 
        3.0, 0.0, 60.0)
    bell_play_milliseconds: int = env_int('CCTV_BELL_PLAY_MILLISECONDS', 
        12000, 100, 60000)
    message_video_max_mb: int = env_int('CCTV_MESSAGE_VIDEO_MAX_MB', 25, 1, 100
        )
    message_video_max_seconds: int = env_int('CCTV_MESSAGE_VIDEO_MAX_SECONDS',
        30, 5, 180)
    message_video_dir: str = os.getenv('CCTV_MESSAGE_VIDEO_DIR',
        'received_messages')
    message_autoplay: bool = env_bool('CCTV_MESSAGE_AUTOPLAY', True)
    message_player_browser: str | None = os.getenv(
        'CCTV_MESSAGE_PLAYER_BROWSER')
    passkeys_enabled: bool = env_bool('CCTV_PASSKEYS_ENABLED', True)
    passkey_store_path: str = os.getenv('CCTV_PASSKEY_STORE',
        'passkeys.local.json')

    @property
    def message_video_max_bytes(self) ->int:
        return self.message_video_max_mb * 1024 * 1024

    @property
    def passkey_store_file(self) ->Path:
        return Path(self.passkey_store_path)


settings = Settings()
templates = Jinja2Templates(directory='templates')


class PasskeyStore:

    def __init__(self, path: Path) ->None:
        self._path = path
        self._lock = threading.Lock()

    def all(self) ->list[dict[str, Any]]:
        with self._lock:
            return self._load()

    def for_rp_id(self, rp_id: str) ->list[dict[str, Any]]:
        return [item for item in self.all() if item.get('rp_id') == rp_id]

    def find(self, credential_id: str, rp_id: str) ->(dict[str, Any] | None):
        for item in self.for_rp_id(rp_id):
            if item.get('credential_id') == credential_id:
                return item
        return None

    def upsert(self, credential: dict[str, Any]) ->None:
        with self._lock:
            credentials = self._load()
            credential_id = credential['credential_id']
            credentials = [item for item in credentials if item.get(
                'credential_id') != credential_id]
            credentials.append(credential)
            self._save(credentials)

    def update_sign_count(self, credential_id: str, rp_id: str, sign_count: int
        ) ->None:
        with self._lock:
            credentials = self._load()
            for item in credentials:
                if item.get('credential_id') == credential_id and item.get(
                    'rp_id') == rp_id:
                    item['sign_count'] = sign_count
                    item['last_used_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ',
                        time.gmtime())
                    break
            self._save(credentials)

    def _load(self) ->list[dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        credentials = data.get('credentials', [])
        return credentials if isinstance(credentials, list) else []

    def _save(self, credentials: list[dict[str, Any]]) ->None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'credentials': credentials}
        self._path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


class LoginThrottle:

    def __init__(self) ->None:
        self._ip_attempts: dict[str, Deque[float]] = defaultdict(deque)
        self._user_attempts: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    def check_rate_limit(self, username: str, ip: str) ->None:
        now = time.monotonic()
        key_user = hashlib.sha256(username.encode()).hexdigest()[:16]
        with self._lock:
            attempts = self._ip_attempts[ip]
            self._discard_old(attempts, now)
            if len(attempts) >= MAX_LOGIN_ATTEMPTS * 2:
                raise HTTPException(status_code=status.
                    HTTP_429_TOO_MANY_REQUESTS, detail=
                    'Too many attempts from this IP')
            if self._user_attempts[key_user] >= MAX_LOGIN_ATTEMPTS:
                raise HTTPException(status_code=status.
                    HTTP_429_TOO_MANY_REQUESTS, detail=
                    'Too many attempts for this username')

    def record_failure(self, username: str, ip: str) ->None:
        now = time.monotonic()
        key_user = hashlib.sha256(username.encode()).hexdigest()[:16]
        with self._lock:
            attempts = self._ip_attempts[ip]
            self._discard_old(attempts, now)
            attempts.append(now)
            self._user_attempts[key_user] += 1

    def clear(self, username: str, ip: str) ->None:
        key_user = hashlib.sha256(username.encode()).hexdigest()[:16]
        with self._lock:
            self._ip_attempts.pop(ip, None)
            self._user_attempts[key_user] = 0

    @staticmethod
    def _discard_old(attempts: Deque[float], now: float) ->None:
        while attempts and now - attempts[0] > LOGIN_WINDOW_SECONDS:
            attempts.popleft()


class CameraManager:

    def __init__(self, config: Settings) ->None:
        self._config = config
        self._lock = threading.Lock()
        self._frame_ready = threading.Condition(self._lock)
        self._capture: Any | None = None
        self._task: Any | None = None
        self._latest_frame = None
        self._latest_jpeg: bytes | None = None
        self._latest_frame_id = 0
        self._viewer_count = 0
        self._jpeg_viewer_count = 0
        self._running = False
        self._last_error: str | None = None
        self.active_filter = 'normal'
        self.ptz = {'cx': 0.5, 'cy': 0.5, 'zoom': 1.0}
        self.quality = 'medium'

    @property
    def status(self) ->dict[str, Any]:
        with self._lock:
            return {'active': self._running, 'viewers': self._viewer_count,
                'frame_width': self._config.frame_width, 'frame_height':
                self._config.frame_height, 'fps': self._config.fps,
                'last_error': self._last_error, 'filter': self.
                active_filter, 'ptz': self.ptz, 'quality': self.quality}

    def add_viewer(self, wants_jpeg: bool=False) ->None:
        with self._lock:
            self._viewer_count += 1
            if wants_jpeg:
                self._jpeg_viewer_count += 1
        try:
            self._ensure_started()
        except Exception:
            with self._lock:
                self._viewer_count = max(0, self._viewer_count - 1)
                if wants_jpeg:
                    self._jpeg_viewer_count = max(0, self.
                        _jpeg_viewer_count - 1)
            raise

    def _ensure_started(self) ->None:
        with self._lock:
            if self._running:
                return
            if self._task is None or self._task.done():
                self._start_locked()

    def remove_viewer(self, wants_jpeg: bool=False) ->None:
        with self._lock:
            self._viewer_count = max(0, self._viewer_count - 1)
            if wants_jpeg:
                self._jpeg_viewer_count = max(0, self._jpeg_viewer_count - 1)
            if self._viewer_count == 0:
                self._running = False
                self._frame_ready.notify_all()

    def get_frame_after(self, last_seen_id: int, timeout: float=2.0) ->tuple[
        int, bytes | None]:
        with self._frame_ready:
            self._frame_ready.wait_for(lambda : self._latest_frame_id !=
                last_seen_id or not self._running, timeout=timeout)
            if self._latest_frame_id == last_seen_id:
                return last_seen_id, None
            return self._latest_frame_id, self._latest_jpeg

    def get_raw_frame_after(self, last_seen_id: int, timeout: float=2.0):
        with self._frame_ready:
            self._frame_ready.wait_for(lambda : self._latest_frame_id !=
                last_seen_id or not self._running, timeout=timeout)
            if self._latest_frame_id == last_seen_id:
                return last_seen_id, None
            frame = self._latest_frame.copy(
                ) if self._latest_frame is not None else None
            return self._latest_frame_id, frame

    def shutdown(self) ->None:
        with self._lock:
            self._viewer_count = 0
            self._running = False
            self._frame_ready.notify_all()
            task = self._task
        if task is not None and not task.done():
            task.cancel()

    def _start_locked(self) ->None:
        capture = self._open_capture()
        if not capture.isOpened():
            self._last_error = (
                'Could not open webcam. Check camera permissions and index.')
            self._task = None
            self._running = False
            capture.release()
            raise RuntimeError(self._last_error)
        self._capture = capture
        self._latest_frame = None
        self._latest_jpeg = None
        self._latest_frame_id = 0
        self._running = True
        self._last_error = None
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._capture_loop())

    def _open_capture(self) ->Any:
        cv2 = get_cv2()
        backend = cv2.CAP_ANY
        capture = cv2.VideoCapture(self._config.camera_index, backend)
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._config.frame_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._config.frame_height)
        capture.set(cv2.CAP_PROP_FPS, self._config.fps)
        return capture

    async def _capture_loop(self) ->None:
        import concurrent.futures
        cv2 = get_cv2()
        frame_interval = 1 / max(1, self._config.fps)
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self._config.
            jpeg_quality]
        queue: asyncio.Queue = asyncio.Queue(maxsize=5)
        loop = asyncio.get_running_loop()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

        def reader():
            while self._running and self._capture:
                loop_started = time.monotonic()
                ok, frame = self._capture.read()
                if not ok:
                    loop.call_soon_threadsafe(queue.put_nowait, (False, None))
                    break
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, (True, frame))
                except asyncio.QueueFull:
                    pass
                elapsed = time.monotonic() - loop_started
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
        executor.submit(reader)
        try:
            while self._running:
                ok, frame = await queue.get()
                with self._lock:
                    wants_jpeg = self._jpeg_viewer_count > 0
                    active_filter = self.active_filter
                    ptz = self.ptz
                    quality = self.quality
                if not ok:
                    with self._frame_ready:
                        self._last_error = 'Webcam stopped returning frames.'
                        self._running = False
                        self._latest_frame = None
                        self._latest_jpeg = None
                        self._frame_ready.notify_all()
                    break
                frame = self._process_frame(cv2, frame, active_filter, ptz)
                encoded_ok = False
                encoded = None
                if wants_jpeg:
                    encoded_ok, encoded = await loop.run_in_executor(executor,
                        cv2.imencode, '.jpg', frame, encode_params)
                with self._frame_ready:
                    self._latest_frame = frame
                    if encoded_ok and encoded is not None:
                        self._latest_jpeg = encoded.tobytes()
                    self._latest_frame_id += 1
                    self._frame_ready.notify_all()
        finally:
            with self._lock:
                capture = self._capture
                self._capture = None
                self._task = None
                self._running = False
            if capture is not None:
                capture.release()
            executor.shutdown(wait=False)

    def _enhance_low_light(self, cv2: Any, frame: Any) ->Any:
        if not self._config.low_light_enhance:
            return frame
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        if brightness >= self._config.low_light_threshold:
            return frame
        gain = min(self._config.low_light_max_gain, self._config.
            low_light_target / max(1.0, brightness))
        return cv2.convertScaleAbs(frame, alpha=gain, beta=self._config.
            low_light_beta)

    def _process_frame(self, cv2: Any, frame: Any, filter_mode: str, ptz: dict
        ) ->Any:
        frame = self._enhance_low_light(cv2, frame)
        h, w = frame.shape[:2]
        zoom = max(1.0, ptz.get('zoom', 1.0))
        if zoom > 1.0:
            cx, cy = ptz.get('cx', 0.5), ptz.get('cy', 0.5)
            nw, nh = int(w / zoom), int(h / zoom)
            x1 = max(0, min(int(cx * w - nw / 2), w - nw))
            y1 = max(0, min(int(cy * h - nh / 2), h - nh))
            frame = cv2.resize(frame[y1:y1 + nh, x1:x1 + nw], (w, h))
        if filter_mode == 'night':
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif filter_mode == 'thermal':
            frame = cv2.applyColorMap(frame, cv2.COLORMAP_JET)
        elif filter_mode == 'grayscale':
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif filter_mode == 'sepia':
            import numpy as np
            kernel = np.array([[0.272, 0.534, 0.131], [0.349, 0.686, 0.168],
                [0.393, 0.769, 0.189]])
            frame = cv2.transform(frame, kernel)
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        elif filter_mode == 'enhance':
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            cl = clahe.apply(l)
            limg = cv2.merge((cl, a, b))
            frame = cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)
        return frame


class CloudflareIceServerProvider:

    def __init__(self, config: Settings) ->None:
        self._config = config
        self._lock = threading.Lock()
        self._cached_until = 0.0
        self._cached_servers: list[dict[str, Any]] | None = None
        self._last_error: str | None = None

    @property
    def enabled(self) ->bool:
        return bool(self._config.cloudflare_turn_key_id and self._config.
            cloudflare_turn_api_token)

    @property
    def last_error(self) ->(str | None):
        with self._lock:
            return self._last_error

    @property
    def relay_ready_cached(self) ->bool:
        with self._lock:
            servers = self._cached_servers
        return self.enabled and self._servers_have_turn(servers or [])

    @property
    def current_ice_mode_cached(self) ->str:
        with self._lock:
            servers = self._cached_servers
        if self.enabled and self._servers_have_turn(servers or []):
            return 'turn'
        if self._fallback_ice_servers():
            return 'stun'
        return 'plain'

    def client_ice_servers(self) ->list[dict[str, Any]]:
        if not self.enabled:
            return self._fallback_ice_servers()
        now = time.monotonic()
        with self._lock:
            if self._cached_servers is not None and now < self._cached_until:
                return self._cached_servers
        servers, fetch_error = self._fetch_cloudflare_ice_servers()
        cache_seconds = min(self._config.ice_cache_seconds, self._config.
            cloudflare_turn_ttl_seconds - 30)
        with self._lock:
            self._cached_servers = servers
            self._cached_until = now + max(0, cache_seconds)
            self._last_error = fetch_error
        return servers

    def rtc_configuration(self) ->Any:
        RTCConfiguration, _, _, _, _ = get_aiortc_classes()
        return RTCConfiguration(iceServers=[self._to_aiortc_server(server) for
            server in self.client_ice_servers()])

    def ice_mode(self, servers: list[dict[str, Any]]) ->str:
        if self._servers_have_turn(servers):
            return 'turn'
        if self._servers_have_stun(servers):
            return 'stun'
        return 'plain'

    def _fallback_ice_servers(self) ->list[dict[str, Any]]:
        if not self._config.enable_stun or not self._config.stun_servers:
            return []
        return [{'urls': list(self._config.stun_servers)}]

    def _fetch_cloudflare_ice_servers(self) ->tuple[list[dict[str, Any]], 
        str | None]:
        url = CLOUDFLARE_TURN_ENDPOINT_TEMPLATE.format(key_id=self._config.
            cloudflare_turn_key_id)
        payload = json.dumps({'ttl': self._config.cloudflare_turn_ttl_seconds}
            ).encode('utf-8')
        request = urlrequest.Request(url, data=payload, headers={
            'Authorization':
            f'Bearer {self._config.cloudflare_turn_api_token}', 'Accept':
            'application/json', 'Content-Type': 'application/json',
            'User-Agent': 'family-cctv/1.0'}, method='POST')
        try:
            with urlrequest.urlopen(request, timeout=8) as response:
                body = json.loads(response.read().decode('utf-8'))
        except urlerror.HTTPError as exc:
            details = ''
            try:
                details = exc.read().decode('utf-8', errors='replace')[:300]
            except Exception:
                pass
            message = (
                f'Cloudflare TURN credential request failed: HTTP {exc.code}')
            if details:
                message = f'{message}: {details}'
            return self._fallback_ice_servers(), message
        except (OSError, TimeoutError, ValueError, urlerror.URLError) as exc:
            message = f'Cloudflare TURN credential request failed: {exc}'
            return self._fallback_ice_servers(), message
        servers = self._extract_ice_servers(body, force_turn_relay=self.
            _config.force_turn_relay)
        if not servers:
            return self._fallback_ice_servers(
                ), 'Cloudflare TURN response did not include ICE servers.'
        return servers, None

    @staticmethod
    def _extract_ice_servers(body: dict[str, Any], force_turn_relay: bool=False
        ) ->list[dict[str, Any]]:
        candidates = body.get('iceServers') or body.get('ice_servers'
            ) or body.get('result', {}).get('iceServers') or body.get('result',
            {}).get('ice_servers')
        if not isinstance(candidates, list):
            return []
        servers: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or 'urls' not in candidate:
                continue
            urls = candidate['urls']
            if isinstance(urls, str):
                urls = [urls]
            if not isinstance(urls, list):
                continue
            cleaned_urls = [url for url in urls if isinstance(url, str) and
                url.startswith(('stun:', 'turn:', 'turns:')) and
                CloudflareIceServerProvider._browser_can_use_ice_url(url)]
            if force_turn_relay:
                cleaned_urls = [url for url in cleaned_urls if url.
                    startswith(('turn:', 'turns:'))]
            cleaned = {'urls': cleaned_urls}
            if not cleaned['urls']:
                continue
            if isinstance(candidate.get('username'), str):
                cleaned['username'] = candidate['username']
            if isinstance(candidate.get('credential'), str):
                cleaned['credential'] = candidate['credential']
            servers.append(cleaned)
        return servers

    @staticmethod
    def _browser_can_use_ice_url(url: str) ->bool:
        address = url.split(':', 1)[1].split('?', 1)[0]
        if address.rsplit(':', 1)[-1] == '53':
            return False
        return True

    @staticmethod
    def _to_aiortc_server(server: dict[str, Any]) ->Any:
        _, RTCIceServer, _, _, _ = get_aiortc_classes()
        return RTCIceServer(urls=server['urls'], username=server.get(
            'username'), credential=server.get('credential'))

    @staticmethod
    def _servers_have_turn(servers: list[dict[str, Any]]) ->bool:
        for server in servers:
            urls = server.get('urls', [])
            if isinstance(urls, str):
                urls = [urls]
            if any(isinstance(url, str) and url.startswith(('turn:',
                'turns:')) for url in urls):
                return True
        return False

    @staticmethod
    def _servers_have_stun(servers: list[dict[str, Any]]) ->bool:
        for server in servers:
            urls = server.get('urls', [])
            if isinstance(urls, str):
                urls = [urls]
            if any(isinstance(url, str) and url.startswith('stun:') for url in
                urls):
                return True
        return False


def create_webcam_video_stream_track(camera_manager: CameraManager, config:
    Settings) ->Any:
    _, _, _, _, VideoStreamTrack = get_aiortc_classes()
    VideoFrame = get_video_frame_class()
    cv2 = get_cv2()
    np = get_numpy()


    class WebcamVideoStreamTrack(VideoStreamTrack):

        def __init__(self) ->None:
            super().__init__()
            self._last_seen_id = 0
            self._closed = False
            camera_manager.add_viewer(wants_jpeg=False)

        async def recv(self) ->Any:
            pts, time_base = await self.next_timestamp()
            frame_id, frame = await asyncio.to_thread(camera_manager.
                get_raw_frame_after, self._last_seen_id, 2.0)
            if frame is None:
                frame = np.zeros((config.frame_height, config.frame_width, 
                    3), dtype=np.uint8)
            else:
                self._last_seen_id = frame_id
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            video_frame = VideoFrame.from_ndarray(frame, format='rgb24')
            video_frame.pts = pts
            video_frame.time_base = time_base
            return video_frame

        def stop(self) ->None:
            if not self._closed:
                self._closed = True
                camera_manager.remove_viewer(wants_jpeg=False)
            super().stop()
    return WebcamVideoStreamTrack()


class DesktopScreenshotter:

    def __init__(self, config: Settings) ->None:
        self._config = config
        self._lock = threading.Lock()

    def capture_png(self) ->bytes:
        try:
            mss, mss_tools = get_mss_modules()
        except ImportError as exc:
            raise RuntimeError(
                'Desktop screenshot support is unavailable. Install the mss package.'
                )
        with self._lock:
            with mss.mss() as screen_capture:
                monitors = screen_capture.monitors
                if not monitors:
                    raise RuntimeError('No desktop monitors were found.')
                monitor_index = self._config.screenshot_monitor
                if monitor_index >= len(monitors):
                    monitor_index = 1 if len(monitors) > 1 else 0
                screenshot = screen_capture.grab(monitors[monitor_index])
                return mss_tools.to_png(screenshot.rgb, screenshot.size)


class BellPlayer:

    def __init__(self, config: Settings) ->None:
        self._config = config
        self._lock = threading.Lock()
        self._last_played_at = 0.0
        self._last_error: str | None = None

    @property
    def last_error(self) ->(str | None):
        with self._lock:
            return self._last_error

    def ring(self) ->dict[str, Any]:
        now = time.monotonic()
        cooldown_seconds = max(self._config.bell_cooldown_seconds, self.
            _config.bell_play_milliseconds / 1000)
        with self._lock:
            remaining = cooldown_seconds - (now - self._last_played_at)
            if remaining > 0:
                return {'played': False, 'cooldown_seconds': round(
                    remaining, 1), 'error': self._last_error}
            self._last_played_at = now
        thread = threading.Thread(target=self._play, name='bell-player',
            daemon=True)
        thread.start()
        return {'played': True, 'cooldown_seconds': 0, 'duration_seconds':
            round(self._config.bell_play_milliseconds / 1000, 1), 'error':
            self.last_error}

    def _set_error(self, message: (str | None)) ->None:
        with self._lock:
            self._last_error = message

    def _play(self) ->None:
        try:
            path = self._config.bell_audio_path
            if path:
                path = os.path.abspath(path)
            if path and os.path.exists(path):
                self._play_file(path)
            else:
                self._play_default_beep()
            self._set_error(None)
        except Exception as exc:
            self._set_error(str(exc))

    def _play_file(self, path: str) ->None:
        if os.name == 'nt' and path.lower().endswith('.wav'):
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                )
            return
        if os.name == 'nt':
            safe_path = path.replace("'", "''")
            command = (
                f"Add-Type -AssemblyName PresentationCore; $player = New-Object System.Windows.Media.MediaPlayer; $player.Open([Uri]'{safe_path}'); $player.Volume = 1.0; $player.Play(); Start-Sleep -Milliseconds {self._config.bell_play_milliseconds}; $player.Close()"
                )
            subprocess.Popen(['powershell', '-NoProfile', '-STA',
                '-WindowStyle', 'Hidden', '-Command', command], stdout=
                subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        raise RuntimeError(
            'Audio file playback is configured only for Windows in this app.')

    def _play_default_beep(self) ->None:
        if os.name == 'nt':
            import winsound
            pattern = (880, 180), (1047, 180), (1319, 320), (1047, 220), (0,
                180)
            end_time = time.monotonic(
                ) + self._config.bell_play_milliseconds / 1000
            while time.monotonic() < end_time:
                for frequency, duration in pattern:
                    if time.monotonic() >= end_time:
                        break
                    if frequency:
                        winsound.Beep(frequency, duration)
                    else:
                        time.sleep(duration / 1000)
            return
        print('\x07', end='')


class VideoMessagePlayer:

    def __init__(self, config: Settings) ->None:
        self._config = config
        self._directory = Path(config.message_video_dir).resolve()
        self._lock = threading.Lock()
        self._last_error: str | None = None
        self._last_video: str | None = None

    @property
    def directory(self) ->Path:
        self._directory.mkdir(parents=True, exist_ok=True)
        return self._directory

    @property
    def last_error(self) ->(str | None):
        with self._lock:
            return self._last_error

    @property
    def last_video(self) ->(str | None):
        with self._lock:
            return self._last_video

    def play(self, video_path: Path) ->dict[str, Any]:
        with self._lock:
            self._last_video = str(video_path)
            self._last_error = None
        if not self._config.message_autoplay:
            return {'started': False, 'error': None}
        thread = threading.Thread(target=self._play, args=(video_path,),
            name='video-message-player', daemon=True)
        thread.start()
        return {'started': True, 'error': None}

    def _set_error(self, message: (str | None)) ->None:
        with self._lock:
            self._last_error = message

    def _play(self, video_path: Path) ->None:
        try:
            if os.name != 'nt':
                raise RuntimeError(
                    'Fullscreen video message playback is configured for Windows.'
                    )
            player_page = self._write_player_page(video_path)
            browser_path = self._find_browser()
            if browser_path:
                subprocess.Popen([browser_path, '--new-window',
                    '--start-fullscreen',
                    '--autoplay-policy=no-user-gesture-required',
                    player_page.as_uri()], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL)
            else:
                os.startfile(str(video_path))
            self._set_error(None)
        except Exception as exc:
            self._set_error(str(exc))

    def _write_player_page(self, video_path: Path) ->Path:
        player_page = self.directory / 'latest-video-message.html'
        video_uri = json.dumps(video_path.resolve().as_uri())
        player_page.write_text(
            f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Family Video Message</title>
  <style>
    html, body {{
      width: 100%;
      height: 100%;
      margin: 0;
      background: #000;
      overflow: hidden;
    }}
    video {{
      width: 100vw;
      height: 100vh;
      object-fit: contain;
      background: #000;
    }}
  </style>
</head>
<body>
  <video id="message" autoplay controls src={video_uri}></video>
  <script>
    const video = document.getElementById("message");
    window.focus();
    video.volume = 1;
    video.play().catch(() => {{}});
    video.addEventListener("ended", () => window.close());
    document.addEventListener("keydown", (event) => {{
      if (event.key === "Escape") window.close();
    }});
  </script>
</body>
</html>
"""
            , encoding='utf-8')
        return player_page

    def _find_browser(self) ->(str | None):
        configured_browser = self._config.message_player_browser
        if configured_browser and Path(configured_browser).exists():
            return configured_browser
        for command in ('msedge', 'chrome'):
            found = shutil.which(command)
            if found:
                return found
        candidates = Path(os.environ.get('ProgramFiles(x86)', '')
            ) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe', Path(os
            .environ.get('ProgramFiles', '')
            ) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe', Path(os
            .environ.get('ProgramFiles', '')
            ) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe', Path(os
            .environ.get('ProgramFiles(x86)', '')
            ) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe'
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return None


class BroadcastPlayer:

    def __init__(self, config: Settings) ->None:
        self._config = config
        self._directory = Path(config.message_video_dir).resolve()

    def broadcast(self, text: str) ->None:
        if os.name != 'nt':
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        broadcast_page = self._directory / 'latest-broadcast.html'
        import html
        escaped_text = html.escape(text)
        html_content = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Family Security Broadcast</title>
  <style>
    html, body {{
      width: 100%;
      height: 100%;
      margin: 0;
      background: #090d16;
      color: #fff;
      font-family: system-ui, -apple-system, sans-serif;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }}
    .container {{
      text-align: center;
      padding: 40px;
      max-width: 80%;
      border-radius: 24px;
      background: rgba(255, 255, 255, 0.04);
      border: 2px solid rgba(16, 185, 129, 0.3);
      box-shadow: 0 0 50px rgba(16, 185, 129, 0.15);
      animation: pulse-border 2s infinite alternate;
    }}
    @keyframes pulse-border {{
      0% {{ border-color: rgba(16, 185, 129, 0.3); box-shadow: 0 0 30px rgba(16, 185, 129, 0.1); }}
      100% {{ border-color: rgba(16, 185, 129, 0.8); box-shadow: 0 0 60px rgba(16, 185, 129, 0.3); }}
    }}
    h1 {{
      font-size: 3rem;
      margin-bottom: 24px;
      background: linear-gradient(135deg, #10b981, #3b82f6);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    p {{
      font-size: 2.5rem;
      font-weight: 700;
      line-height: 1.4;
      margin: 0 0 30px 0;
    }}
    .close-hint {{
      font-size: 1.2rem;
      color: rgba(255, 255, 255, 0.4);
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>🔔 अभिभावकको सन्देश:</h1>
    <p>{escaped_text}</p>
    <div class="close-hint">यो विन्डो बन्द गर्न स्क्रिनमा क्लिक गर्नुहोस् वा 'Escape' कुञ्जी थिच्नुहोस्</div>
  </div>

  <script>
    window.focus();
    try {{
      const speech = new SpeechSynthesisUtterance("New message: " + {json.dumps(text)});
      window.speechSynthesis.speak(speech);
    }} catch(e) {{}}

    document.addEventListener("keydown", (event) => {{
      if (event.key === "Escape") window.close();
    }});
    document.body.addEventListener("click", () => {{
      window.close();
    }});
  </script>
</body>
</html>
"""
        broadcast_page.write_text(html_content, encoding='utf-8')
        browser_path = video_message_player._find_browser()
        if browser_path:
            subprocess.Popen([browser_path, '--new-window',
                '--start-fullscreen',
                '--autoplay-policy=no-user-gesture-required',
                broadcast_page.as_uri()], stdout=subprocess.DEVNULL, stderr
                =subprocess.DEVNULL)
        else:
            os.startfile(str(broadcast_page))

    def siren(self) ->None:
        if os.name != 'nt':
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        siren_page = self._directory / 'latest-siren.html'
        html_content = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>EMERGENCY SIREN</title>
  <style>
    html, body {
      width: 100%;
      height: 100%;
      margin: 0;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      font-family: system-ui, sans-serif;
      animation: flash-bg 0.5s infinite steps(2, start);
    }
    @keyframes flash-bg {
      0% { background: #ff0000; color: #fff; }
      50% { background: #0000ff; color: #fff; }
    }
    .card {
      background: rgba(0, 0, 0, 0.85);
      padding: 50px 80px;
      border-radius: 30px;
      text-align: center;
      box-shadow: 0 0 100px rgba(255, 255, 255, 0.5);
      border: 5px solid #fff;
    }
    h1 {
      font-size: 5rem;
      margin: 0 0 20px 0;
      letter-spacing: 2px;
      animation: zoom 0.5s infinite alternate;
    }
    p {
      font-size: 2.5rem;
      font-weight: 800;
      margin: 0 0 30px 0;
    }
    .close-btn {
      padding: 15px 40px;
      font-size: 1.5rem;
      font-weight: 700;
      background: #fff;
      color: #000;
      border: none;
      border-radius: 15px;
      cursor: pointer;
    }
    @keyframes zoom {
      from { transform: scale(0.95); }
      to { transform: scale(1.05); }
    }
  </style>
</head>
<body>
  <div class="card">
    <h1>🚨 ALERT 🚨</h1>
    <p>सङ्कटकालीन साइरन बजिरहेको छ!</p>
    <button class="close-btn" onclick="window.close()">बन्द गर्नुहोस् (ESC)</button>
  </div>

  <script>
    window.focus();

    let audioCtx = null;
    let osc = null;
    let gainNode = null;
    let intervalId = null;

    function startSiren() {
      try {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        osc = audioCtx.createOscillator();
        gainNode = audioCtx.createGain();

        osc.connect(gainNode);
        gainNode.connect(audioCtx.destination);

        osc.type = 'sine';
        osc.frequency.setValueAtTime(600, audioCtx.currentTime);
        gainNode.gain.setValueAtTime(0.5, audioCtx.currentTime);

        osc.start();

        let time = 0;
        intervalId = setInterval(() => {
          if (osc && audioCtx) {
            let freq = 850 + Math.sin(time) * 350;
            osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
            time += 0.25;
          }
        }, 30);
      } catch(e) {
        console.error("Audio API error:", e);
      }
    }

    window.addEventListener('load', () => {
      startSiren();
    });

    document.body.addEventListener("click", () => {
      if (!audioCtx) startSiren();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        if (intervalId) clearInterval(intervalId);
        window.close();
      }
    });

    window.addEventListener("beforeunload", () => {
      if (intervalId) clearInterval(intervalId);
    });
  </script>
</body>
</html>
"""
        siren_page.write_text(html_content, encoding='utf-8')
        browser_path = video_message_player._find_browser()
        if browser_path:
            subprocess.Popen([browser_path, '--new-window',
                '--start-fullscreen',
                '--autoplay-policy=no-user-gesture-required', siren_page.
                as_uri()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
        else:
            os.startfile(str(siren_page))


@dataclass
class PeerSession:
    pc: Any
    track: Any
    last_seen: float


camera = CameraManager(settings)
ice_servers = CloudflareIceServerProvider(settings)
desktop_screenshotter = DesktopScreenshotter(settings)
bell_player = BellPlayer(settings)
video_message_player = VideoMessagePlayer(settings)
broadcast_player = BroadcastPlayer(settings)
login_throttle = LoginThrottle()
passkey_store = PasskeyStore(settings.passkey_store_file)
peer_connections: dict[str, PeerSession] = {}


class SSEManager:

    def __init__(self):
        self.queues: list[asyncio.Queue] = []
        self._lock = threading.Lock()

    def add_queue(self) ->asyncio.Queue:
        q = asyncio.Queue()
        with self._lock:
            self.queues.append(q)
        return q

    def remove_queue(self, q: asyncio.Queue) ->None:
        with self._lock:
            if q in self.queues:
                self.queues.remove(q)

    def broadcast(self, event: dict[str, Any]) ->None:
        with self._lock:
            for q in self.queues:
                q.put_nowait(event)


sse_manager = SSEManager()


async def close_peer(peer_id: str) ->None:
    session = peer_connections.pop(peer_id, None)
    if not session:
        return
    try:
        session.track.stop()
    except Exception:
        logger.exception('Could not stop WebRTC track %s', peer_id)
    try:
        if session.pc.connectionState != 'closed':
            await session.pc.close()
    except Exception:
        logger.exception('Could not close WebRTC peer %s', peer_id)


async def peer_watchdog() ->None:
    while True:
        await asyncio.sleep(max(5, settings.webrtc_heartbeat_seconds))
        now = time.monotonic()
        stale_peer_ids = [peer_id for peer_id, session in list(
            peer_connections.items()) if now - session.last_seen > settings
            .webrtc_peer_timeout_seconds]
        if stale_peer_ids:
            sse_manager.broadcast({'type': 'session_expired', 'peers':
                stale_peer_ids})
        await asyncio.gather(*(close_peer(peer_id) for peer_id in
            stale_peer_ids), return_exceptions=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    watchdog_task = asyncio.create_task(peer_watchdog())
    try:
        yield
    finally:
        watchdog_task.cancel()
        await asyncio.gather(watchdog_task, return_exceptions=True)
        await asyncio.gather(*(close_peer(peer_id) for peer_id in list(
            peer_connections)), return_exceptions=True)
        peer_connections.clear()
        camera.shutdown()


app = FastAPI(title=APP_NAME, lifespan=lifespan)
app.mount('/static', StaticFiles(directory='static'), name='static')
if settings.allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.
        allowed_hosts))
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret or
    secrets.token_urlsafe(32), https_only=settings.cookie_secure, same_site
    ='strict', max_age=60 * 60 * 8)


def ensure_configured() ->None:
    if not settings.password:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=
            'CCTV_PASSWORD is not set. Set it before running the server.')


def client_key(request: Request) ->str:
    forwarded_for = request.headers.get('x-forwarded-for')
    if settings.trust_proxy_https and forwarded_for:
        return forwarded_for.split(',', 1)[0].strip()
    return request.client.host if request.client else 'unknown'


def is_authenticated(request: Request) ->bool:
    return request.session.get(AUTH_SESSION_KEY) is True


def require_auth(request: Request) ->None:
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Login required')


def request_host(request: Request) ->str:
    return request.headers.get('host', '').split(':', 1)[0].strip().lower()


def request_origin(request: Request) ->str:
    host = request.headers.get('host', '').strip()
    scheme = request.headers.get('x-forwarded-proto', '').split(',', 1)[0
        ].strip()
    if not scheme:
        scheme = 'https' if settings.trust_proxy_https and not is_local_host(
            request_host(request)) else request.url.scheme
    return f'{scheme}://{host}'


def is_local_host(host: str) ->bool:
    return host in {'localhost', '127.0.0.1', '::1'}


def cloudflare_access_allowed(request: Request) ->bool:
    if not settings.require_cloudflare_access:
        return True
    host = request_host(request)
    if is_local_host(host):
        return True
    protected_hosts = {item.lower() for item in settings.
        cloudflare_access_hosts}
    if protected_hosts and host not in protected_hosts:
        return True
    allowed_emails = {item.lower() for item in settings.
        cloudflare_access_emails}
    if not allowed_emails:
        return False
    email = request.headers.get('cf-access-authenticated-user-email', ''
        ).strip().lower()
    return email in allowed_emails


def webauthn_ready() ->bool:
    return bool(WEBAUTHN_AVAILABLE and settings.passkeys_enabled)


def passkey_user_id() ->bytes:
    seed = f'{settings.username}:{settings.session_secret or APP_NAME}'.encode(
        'utf-8')
    return hashlib.sha256(seed).digest()[:32]


def passkey_state(rp_id: str, origin: str, challenge: bytes) ->dict[str, str]:
    return {'rp_id': rp_id, 'origin': origin, 'challenge': 
        bytes_to_base64url(challenge) if WEBAUTHN_AVAILABLE else ''}


def get_passkey_challenge(session: dict[str, Any], key: str) ->tuple[str,
    str, bytes]:
    state = session.pop(key, None)
    if not isinstance(state, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail
            ='Passkey challenge expired.')
    rp_id = state.get('rp_id')
    origin = state.get('origin')
    challenge = state.get('challenge')
    if not isinstance(rp_id, str) or not isinstance(origin, str
        ) or not isinstance(challenge, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail
            ='Passkey challenge expired.')
    return rp_id, origin, base64url_to_bytes(challenge)


def security_headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Permissions-Policy'
        ] = 'camera=(self), microphone=(self), publickey-credentials-get=(self)'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
    if settings.hsts:
        response.headers['Strict-Transport-Security'
            ] = 'max-age=31536000; includeSubDomains'
    return response


async def trigger_motion_alert(config: Settings):
    logger.info('Motion detected!')
    if config.telegram_bot_token and config.telegram_chat_id:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.post(
                    f'https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage'
                    , json={'chat_id': config.telegram_chat_id, 'text':
                    '🚨 Motion Detected by Family CCTV!'})
        except Exception as e:
            logger.error(f'Telegram alert failed: {e}')
    if push_subscriptions and config.vapid_private_key:
        import pywebpush
        for sub in list(push_subscriptions):
            try:
                pywebpush.webpush(subscription_info=sub, data=
                    'Motion Detected!', vapid_private_key=config.
                    vapid_private_key, vapid_claims={'sub':
                    f'mailto:{config.vapid_email}'})
            except pywebpush.WebPushException as ex:
                logger.error(f'Push failed: {repr(ex)}')


push_subscriptions = []


@app.post('/api/subscribe')
async def subscribe_push(request: Request):
    require_auth(request)
    sub = await request.json()
    if sub not in push_subscriptions:
        push_subscriptions.append(sub)
    return {'status': 'ok'}


@app.post('/api/settings/privacy')
async def set_privacy(request: Request):
    require_auth(request)
    payload = await request.json()
    with camera._lock:
        camera.privacy_mode = bool(payload.get('enabled', False))
    return {'status': 'ok', 'privacy_mode': camera.privacy_mode}


@app.post('/api/settings/filter')
async def set_filter(request: Request):
    require_auth(request)
    payload = await request.json()
    with camera._lock:
        camera.active_filter = payload.get('filter', 'normal')
    return {'status': 'ok', 'filter': camera.active_filter}


@app.post('/api/settings/ptz')
async def set_ptz(request: Request):
    require_auth(request)
    payload = await request.json()
    with camera._lock:
        camera.ptz['cx'] = float(payload.get('cx', 0.5))
        camera.ptz['cy'] = float(payload.get('cy', 0.5))
        camera.ptz['zoom'] = float(payload.get('zoom', 1.0))
    return {'status': 'ok', 'ptz': camera.ptz}


@app.post('/api/settings/quality')
async def set_quality(request: Request):
    require_auth(request)
    payload = await request.json()
    with camera._lock:
        camera.quality = payload.get('quality', 'medium')
    return {'status': 'ok', 'quality': camera.quality}


@app.post('/api/settings/motion')
async def set_motion(request: Request):
    require_auth(request)
    payload = await request.json()
    with camera._lock:
        camera.motion_enabled = bool(payload.get('enabled', False))
    return {'status': 'ok', 'motion_enabled': camera.motion_enabled}


from itsdangerous import TimestampSigner


@app.post('/api/settings/guest')
async def set_guest(request: Request):
    require_auth(request)
    payload = await request.json()
    expiry_minutes = int(payload.get('expiry_minutes', 60))
    secret = settings.session_secret or 'default_secret'
    signer = TimestampSigner(secret)
    token = signer.sign('guest').decode('utf-8')
    url = f'{request_origin(request)}/guest/{token}'
    return {'status': 'ok', 'url': url, 'expires_in_minutes': expiry_minutes}


@app.get('/guest/{token}')
async def guest_viewer(request: Request, token: str):
    secret = settings.session_secret or 'default_secret'
    signer = TimestampSigner(secret)
    try:
        signer.unsign(token, max_age=3600)
    except Exception:
        raise HTTPException(status_code=403, detail=
            'Guest token is invalid or expired.')
    response = templates.TemplateResponse('viewer.html', {'request':
        request, 'app_name': f'{APP_NAME} (Guest)', 'status': camera.status,
        'live_idle_stop_seconds': settings.live_idle_stop_seconds,
        'message_video_max_seconds': settings.message_video_max_seconds,
        'message_video_max_mb': settings.message_video_max_mb, 'is_guest': 
        True})
    return security_headers(response)


@app.post('/api/settings/pin')
async def set_pin(request: Request):
    require_auth(request)
    payload = await request.json()
    pin = payload.get('pin')
    if not pin or not str(pin).isdigit() or len(str(pin)) < 4:
        raise HTTPException(status_code=400, detail=
            'Invalid PIN. Must be at least 4 digits.')
    import bcrypt
    pin_hash = bcrypt.hashpw(str(pin).encode('utf-8'), bcrypt.gensalt()
        ).decode('utf-8')
    try:
        with open('pin_store.json', 'w') as f:
            json.dump({'pin_hash': pin_hash}, f)
    except Exception as e:
        logger.error(f'Failed to save PIN: {e}')
        raise HTTPException(status_code=500, detail='Failed to save PIN.')
    return {'status': 'ok'}


@app.get('/api/session/log')
async def get_session_log(request: Request):
    require_auth(request)
    logs = []
    try:
        if os.path.exists('session_log.jsonl'):
            with open('session_log.jsonl', 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        logs.append(json.loads(line))
    except Exception as e:
        logger.error(f'Failed to read session log: {e}')
    return {'logs': list(reversed(logs))}


@app.get('/api/clip')
async def download_clip(request: Request):
    require_auth(request)
    if not camera.clip_buffer:
        raise HTTPException(status_code=404, detail='No frames available')
    frames = list(camera.clip_buffer)
    import tempfile
    td = tempfile.TemporaryDirectory()
    try:
        cv2 = get_cv2()
        for i, frame in enumerate(frames):
            if frame is not None:
                cv2.imwrite(os.path.join(td.name, f'{i:04d}.jpg'), frame)
        mp4_path = os.path.join(td.name, 'out.mp4')
        subprocess.run(['ffmpeg', '-y', '-r', str(settings.fps), '-i', os.
            path.join(td.name, '%04d.jpg'), '-vcodec', 'libx264',
            '-pix_fmt', 'yuv420p', mp4_path], check=True, capture_output=True)
        return FileResponse(mp4_path, media_type='video/mp4', filename=
            'clip.mp4', background=BackgroundTask(td.cleanup))
    except Exception as e:
        td.cleanup()
        logger.error(f'Failed to generate clip: {e}')
        raise HTTPException(status_code=500, detail='Clip generation failed')


@app.middleware('http')
async def add_security_headers(request: Request, call_next):
    if not cloudflare_access_allowed(request):
        return security_headers(JSONResponse({'detail':
            'Cloudflare Access login required for this CCTV site.'},
            status_code=status.HTTP_403_FORBIDDEN))
    response = await call_next(request)
    return security_headers(response)


@app.get('/', response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse('/viewer', status_code=status.HTTP_302_FOUND)
    response = templates.TemplateResponse('login.html', {'request': request,
        'app_name': APP_NAME, 'configured': bool(settings.password)})
    return security_headers(response)


@app.post('/login')
def login(request: Request, username: str=Form(...), password: str=Form(...)):
    ensure_configured()
    ip = client_key(request)
    login_throttle.check_rate_limit(username, ip)
    valid_username = secrets.compare_digest(username, settings.username)
    valid_password = secrets.compare_digest(password, settings.password or '')
    if valid_username and valid_password:
        login_throttle.clear(username, ip)
        request.session[AUTH_SESSION_KEY] = True
        log_session_event('login_success', {'ip': ip, 'username': username})
        response = RedirectResponse('/viewer', status_code=status.
            HTTP_302_FOUND)
        return security_headers(response)
    login_throttle.record_failure(username, ip)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=
        'Invalid credentials')


@app.post('/api/settings/schedule')
async def set_schedule(request: Request):
    require_auth(request)
    payload = await request.json()
    start_hour = payload.get('start_hour')
    end_hour = payload.get('end_hour')
    for job in camera.scheduler.get_jobs():
        job.remove()
    if start_hour is not None and end_hour is not None:
        camera.scheduler.add_job(lambda : setattr(camera, 'motion_enabled',
            True), CronTrigger(hour=start_hour))
        camera.scheduler.add_job(lambda : setattr(camera, 'motion_enabled',
            False), CronTrigger(hour=end_hour))
    return {'status': 'ok'}


@app.post('/logout')
def logout(request: Request):
    log_session_event('logout', {'ip': client_key(request)})
    request.session.clear()
    response = RedirectResponse('/', status_code=status.HTTP_302_FOUND)
    return security_headers(response)


@app.get('/passkey/status')
def passkey_status(request: Request):
    rp_id = request_host(request)
    return {'available': webauthn_ready(), 'registered': bool(passkey_store
        .for_rp_id(rp_id)) if webauthn_ready() else False}


@app.post('/passkey/register/options')
def passkey_register_options(request: Request):
    require_auth(request)
    if not webauthn_ready():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Passkeys are not available.')
    rp_id = request_host(request)
    credentials = passkey_store.for_rp_id(rp_id)
    exclude_credentials = [PublicKeyCredentialDescriptor(id=
        base64url_to_bytes(item['credential_id'])) for item in credentials if
        isinstance(item.get('credential_id'), str)]
    options = generate_registration_options(rp_id=rp_id, rp_name=APP_NAME,
        user_name=settings.username, user_id=passkey_user_id(),
        user_display_name='Family CCTV parent', authenticator_selection=
        AuthenticatorSelectionCriteria(resident_key=ResidentKeyRequirement.
        PREFERRED, user_verification=UserVerificationRequirement.PREFERRED),
        exclude_credentials=exclude_credentials, hints=[
        PublicKeyCredentialHint.CLIENT_DEVICE])
    request.session[PASSKEY_REGISTRATION_CHALLENGE_KEY] = passkey_state(rp_id
        =rp_id, origin=request_origin(request), challenge=options.challenge)
    return JSONResponse(json.loads(options_to_json(options)))


@app.post('/passkey/register/verify')
async def passkey_register_verify(request: Request, payload: dict[str, Any]
    =Body(...)):
    require_auth(request)
    if not webauthn_ready():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Passkeys are not available.')
    rp_id, origin, challenge = get_passkey_challenge(request.session,
        PASSKEY_REGISTRATION_CHALLENGE_KEY)
    try:
        verified = verify_registration_response(credential=payload,
            expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, require_user_verification=False)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail
            =f'Passkey setup failed: {exc}') from exc
    credential_id = bytes_to_base64url(verified.credential_id)
    passkey_store.upsert({'credential_id': credential_id, 'public_key':
        bytes_to_base64url(verified.credential_public_key), 'sign_count':
        verified.sign_count, 'rp_id': rp_id, 'created_at': time.strftime(
        '%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'last_used_at': None,
        'device_type': str(verified.credential_device_type.value),
        'backed_up': bool(verified.credential_backed_up)})
    return {'ok': True, 'credentialId': credential_id}


@app.post('/passkey/authenticate/options')
def passkey_authenticate_options(request: Request):
    if not webauthn_ready():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Passkeys are not available.')
    rp_id = request_host(request)
    credentials = passkey_store.for_rp_id(rp_id)
    if not credentials:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=
            'No passkey is set up for this site.')
    allow_credentials = [PublicKeyCredentialDescriptor(id=
        base64url_to_bytes(item['credential_id'])) for item in credentials if
        isinstance(item.get('credential_id'), str)]
    options = generate_authentication_options(rp_id=rp_id,
        allow_credentials=allow_credentials, user_verification=
        UserVerificationRequirement.PREFERRED)
    request.session[PASSKEY_AUTHENTICATION_CHALLENGE_KEY] = passkey_state(rp_id
        =rp_id, origin=request_origin(request), challenge=options.challenge)
    return JSONResponse(json.loads(options_to_json(options)))


@app.post('/passkey/authenticate/verify')
async def passkey_authenticate_verify(request: Request, payload: dict[str,
    Any]=Body(...)):
    if not webauthn_ready():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Passkeys are not available.')
    ip = client_key(request)
    login_throttle.check_rate_limit(settings.username, ip)
    rp_id, origin, challenge = get_passkey_challenge(request.session,
        PASSKEY_AUTHENTICATION_CHALLENGE_KEY)
    credential_id = payload.get('id') or payload.get('rawId')
    if not isinstance(credential_id, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail
            ='Missing passkey credential.')
    credential = passkey_store.find(credential_id, rp_id)
    if credential is None:
        login_throttle.record_failure(settings.username, ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Unknown passkey.')
    try:
        verified = verify_authentication_response(credential=payload,
            expected_challenge=challenge, expected_rp_id=rp_id,
            expected_origin=origin, credential_public_key=
            base64url_to_bytes(credential['public_key']),
            credential_current_sign_count=int(credential.get('sign_count') or
            0), require_user_verification=False)
    except Exception as exc:
        login_throttle.record_failure(settings.username, ip)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f'Passkey login failed: {exc}') from exc
    login_throttle.clear(settings.username, ip)
    passkey_store.update_sign_count(credential_id, rp_id, verified.
        new_sign_count)
    request.session[AUTH_SESSION_KEY] = True
    return {'ok': True}


@app.get('/viewer', response_class=HTMLResponse)
def viewer(request: Request):
    if not is_authenticated(request):
        return RedirectResponse('/', status_code=status.HTTP_302_FOUND)
    response = templates.TemplateResponse('viewer.html', {'request':
        request, 'app_name': APP_NAME, 'status': camera.status,
        'live_idle_stop_seconds': settings.live_idle_stop_seconds,
        'message_video_max_seconds': settings.message_video_max_seconds,
        'message_video_max_mb': settings.message_video_max_mb})
    return security_headers(response)


@app.get('/status')
def camera_status(request: Request):
    require_auth(request)
    current_status = camera.status
    current_status['webrtc_peers'] = len(peer_connections)
    current_status['cloudflare_turn'] = ice_servers.enabled
    current_status['turn_relay_ready'] = ice_servers.relay_ready_cached
    current_status['ice_mode'] = ice_servers.current_ice_mode_cached
    current_status['stun_enabled'] = bool(settings.enable_stun and settings
        .stun_servers)
    current_status['ice_error'] = ice_servers.last_error
    current_status['bell_error'] = bell_player.last_error
    current_status['message_error'] = video_message_player.last_error
    current_status['live_idle_stop_seconds'] = settings.live_idle_stop_seconds
    current_status['message_video_max_seconds'
        ] = settings.message_video_max_seconds
    current_status['message_video_max_mb'] = settings.message_video_max_mb
    current_status['http_debug_stream'] = True
    return current_status


@app.post('/desktop/screenshot')
async def desktop_screenshot(request: Request):
    require_auth(request)
    try:
        png = await asyncio.to_thread(desktop_screenshotter.capture_png)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f'Could not capture desktop screenshot: {exc}') from exc
    return Response(content=png, media_type='image/png', headers={
        'Cache-Control': 'no-store', 'Content-Disposition':
        'inline; filename="desktop-screenshot.png"'})


@app.post('/bell/ring')
async def ring_bell(request: Request):
    require_auth(request)
    return bell_player.ring()


@app.post('/message/video')
async def upload_video_message(request: Request, file: UploadFile=File(...)):
    require_auth(request)
    content_type = (file.content_type or '').split(';', 1)[0].strip().lower()
    extension = video_extension(content_type, file.filename)
    if extension is None:
        raise HTTPException(status_code=status.
            HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=
            'Upload a WebM, MP4, MOV, or MKV video message.')
    video_dir = video_message_player.directory
    video_path = (video_dir /
        f"message-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}.{extension}"
        )
    written = 0
    now = time.time()
    existing_files = list(video_dir.glob('message-*.*'))
    existing_files.sort(key=lambda x: x.stat().st_mtime)
    for f in existing_files:
        if now - f.stat().st_mtime > 7 * 86400:
            f.unlink(missing_ok=True)
            existing_files.remove(f)
    while len(existing_files) >= 20:
        existing_files[0].unlink(missing_ok=True)
        existing_files.pop(0)
    try:
        with video_path.open('wb') as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > settings.message_video_max_bytes:
                    raise HTTPException(status_code=status.
                        HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=
                        f'Video message is larger than {settings.message_video_max_mb} MB.'
                        )
                output.write(chunk)
        if shutil.which('ffmpeg') and video_path.suffix != '.mp4':
            mp4_path = video_path.with_suffix('.mp4')
            try:
                subprocess.run(['ffmpeg', '-i', str(video_path), '-vcodec',
                    'libx264', '-acodec', 'aac', '-y', str(mp4_path)],
                    check=True, capture_output=True)
                video_path.unlink(missing_ok=True)
                video_path = mp4_path
            except Exception as e:
                logger.error(f'Transcoding failed: {e}')
    except Exception:
        video_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    if written == 0:
        video_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail
            ='Video message was empty.')
    play_result = await asyncio.to_thread(video_message_player.play, video_path
        )
    return {'saved': True, 'bytes': written, 'maxBytes': settings.
        message_video_max_bytes, 'autoplayStarted': play_result['started'],
        'playerError': play_result['error']}


@app.get('/webrtc/config')
async def webrtc_config(request: Request):
    require_auth(request)
    servers = await asyncio.to_thread(ice_servers.client_ice_servers)
    ice_mode = ice_servers.ice_mode(servers)
    return {'iceServers': servers, 'cloudflareTurn': ice_servers.enabled,
        'turnRelayReady': ice_mode == 'turn', 'forceTurnRelay': settings.
        force_turn_relay, 'stunEnabled': bool(settings.enable_stun and
        settings.stun_servers), 'iceMode': ice_mode, 'iceError':
        ice_servers.last_error}


@app.post('/webrtc/offer')
async def webrtc_offer(request: Request, payload: WebRTCOfferPayload):
    require_auth(request)
    offer_sdp = payload.sdp
    offer_type = payload.type
    if offer_type != 'offer':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail
            ='Invalid WebRTC offer.')
    _, _, RTCPeerConnection, RTCSessionDescription, _ = get_aiortc_classes()
    configuration = await asyncio.to_thread(ice_servers.rtc_configuration)
    pc = RTCPeerConnection(configuration=configuration)
    peer_id = secrets.token_urlsafe(16)
    track = create_webcam_video_stream_track(camera, settings)
    peer_connections[peer_id] = PeerSession(pc=pc, track=track, last_seen=
        time.monotonic())

    @pc.on('connectionstatechange')
    async def on_connectionstatechange() ->None:
        if pc.connectionState in {'failed', 'closed', 'disconnected'}:
            await close_peer(peer_id)

    @pc.on('iceconnectionstatechange')
    async def on_iceconnectionstatechange() ->None:
        if pc.iceConnectionState in {'failed', 'closed', 'disconnected'}:
            await close_peer(peer_id)
    try:
        pc.addTrack(track)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp,
            type=offer_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
    except Exception as exc:
        await close_peer(peer_id)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f'Could not start WebRTC stream: {exc}') from exc
    return JSONResponse({'sdp': pc.localDescription.sdp, 'type': pc.
        localDescription.type, 'peerId': peer_id, 'heartbeatSeconds':
        settings.webrtc_heartbeat_seconds, 'peerTimeoutSeconds': settings.
        webrtc_peer_timeout_seconds})


@app.post('/webrtc/heartbeat')
async def webrtc_heartbeat(request: Request, payload: WebRTCPeerPayload):
    require_auth(request)
    peer_id = payload.peerId
    session = peer_connections.get(peer_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=
            'WebRTC peer is no longer active.')
    session.last_seen = time.monotonic()
    return {'ok': True}


@app.post('/webrtc/stop')
async def webrtc_stop(request: Request, payload: WebRTCPeerPayload):
    require_auth(request)
    peer_id = payload.peerId
    if isinstance(peer_id, str):
        await close_peer(peer_id)
    return {'ok': True}


@app.get('/video')
def video(request: Request):
    require_auth(request)

    def stream():
        added = False
        last_seen_id = 0
        try:
            camera.add_viewer(wants_jpeg=True)
            added = True
            while True:
                frame_id, jpeg = camera.get_frame_after(last_seen_id)
                if jpeg is None:
                    continue
                last_seen_id = frame_id
                yield b'--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-store\r\n\r\n' + jpeg + b'\r\n'
        finally:
            if added:
                camera.remove_viewer(wants_jpeg=True)
    return StreamingResponse(stream(), media_type=
        'multipart/x-mixed-replace; boundary=frame', headers={
        'Cache-Control': 'no-store'})


@app.get('/battery')
def get_battery(request: Request):
    require_auth(request)
    if os.name != 'nt':
        return {'battery': '100%', 'charging': True}
    try:
        cmd = (
            '$b = Get-WmiObject Win32_Battery; if ($b) { "$($b.EstimatedChargeRemaining),$(""2"" -eq $b.BatteryStatus)" } else { "100,True" }'
            )
        res = subprocess.run(['powershell', '-NoProfile', '-Command', cmd],
            capture_output=True, text=True)
        out = res.stdout.strip()
        if out and ',' in out:
            charge, charging = out.split(',')
            return {'battery': f'{charge}%', 'charging': charging == 'True'}
    except Exception:
        pass
    return {'battery': '100%', 'charging': True}


@app.post('/control/lock')
def control_lock(request: Request):
    require_auth(request)
    if os.name == 'nt':
        subprocess.run(['rundll32.exe', 'user32.dll,LockWorkStation'])
    return {'ok': True}


@app.post('/control/mute')
def control_mute(request: Request):
    require_auth(request)
    if os.name == 'nt':
        subprocess.run(['powershell', '-NoProfile', '-Command',
            '(New-Object -ComObject WScript.Shell).SendKeys([char]173)'])
    return {'ok': True}


@app.post('/control/close_app')
def control_close_app(request: Request):
    require_auth(request)
    if os.name == 'nt':
        subprocess.run(['powershell', '-NoProfile', '-Command',
            "(New-Object -ComObject WScript.Shell).SendKeys('%{F4}')"])
    return {'ok': True}


@app.get('/host/stats')
def host_stats(request: Request):
    require_auth(request)
    cpu = 0
    ram_percent = 0
    disk_percent = 0
    battery = 100
    charging = False
    if os.name == 'nt':
        try:
            res = subprocess.run(['powershell', '-NoProfile', '-Command',
                'Get-CimInstance Win32_Processor | Select-Object -ExpandProperty LoadPercentage'
                ], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                cpu = int(res.stdout.strip())
        except Exception:
            pass
        try:
            res = subprocess.run(['powershell', '-NoProfile', '-Command',
                '$os = Get-CimInstance Win32_OperatingSystem; [int]((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / $os.TotalVisibleMemorySize) * 100)'
                ], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                ram_percent = int(res.stdout.strip())
        except Exception:
            pass
        try:
            res = subprocess.run(['powershell', '-NoProfile', '-Command',
                '$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID=\'C:\'"; [int]((( $disk.Size - $disk.FreeSpace ) / $disk.Size ) * 100)'
                ], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                disk_percent = int(res.stdout.strip())
        except Exception:
            pass
        try:
            res = subprocess.run(['powershell', '-NoProfile', '-Command',
                '$batt = Get-CimInstance Win32_BatteryStatus; if ($batt) { "$($batt.EstimatedChargeRemaining),$($batt.PowerOnline)" } else { "100,True" }'
                ], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                parts = res.stdout.strip().split(',')
                battery = int(parts[0]) if parts[0] else 100
                charging = parts[1].lower() == 'true' if len(parts
                    ) > 1 else False
        except Exception:
            pass
    return {'cpu': cpu, 'ram': ram_percent, 'disk': disk_percent, 'battery':
        battery, 'charging': charging, 'uptime': int(time.time() -
        startup_time)}


@app.post('/control/speak')
async def control_speak(request: Request, payload: SpeakPayload):
    require_auth(request)
    text = payload.text
    if os.name == 'nt':
        safe_text = text.replace("'", "''").replace('"', '""')
        command = (
            f'Add-Type -AssemblyName System.Speech; $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; $synth.Speak("{safe_text}")'
            )
        subprocess.Popen(['powershell', '-NoProfile', '-WindowStyle',
            'Hidden', '-Command', command], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    return {'ok': True}


@app.post('/control/broadcast')
async def control_broadcast(request: Request, payload: BroadcastPayload):
    require_auth(request)
    text = payload.text
    if os.name == 'nt':
        await asyncio.to_thread(broadcast_player.broadcast, text)
    return {'ok': True}


@app.post('/control/siren')
async def control_siren(request: Request):
    require_auth(request)
    if os.name == 'nt':
        threading.Thread(target=broadcast_player.siren, name=
            'siren-launcher', daemon=True).start()

        def play_host_beeps():
            import winsound
            for _ in range(8):
                winsound.Beep(1200, 250)
                winsound.Beep(900, 250)
        threading.Thread(target=play_host_beeps, name='siren-beeper',
            daemon=True).start()
    return {'ok': True}


@app.get('/health')
def health():
    return {'ok': True}


@app.get('/health/camera')
async def camera_health():
    cv2 = get_cv2()
    backend = cv2.CAP_DSHOW if os.name == 'nt' else cv2.CAP_ANY
    cap = cv2.VideoCapture(settings.camera_index, backend)
    ok = cap.isOpened()
    if ok:
        cap.release()
    return {'status': 'ok' if ok else 'unavailable', 'index': settings.
        camera_index}


@app.get('/events')
async def sse_events(request: Request):
    require_auth(request)
    q = sse_manager.add_queue()

    async def generator():
        try:
            while True:
                event = await q.get()
                yield f'data: {json.dumps(event)}\n\n'
        except asyncio.CancelledError:
            pass
        finally:
            sse_manager.remove_queue(q)
    return StreamingResponse(generator(), media_type='text/event-stream')
