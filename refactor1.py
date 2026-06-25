import re

with open('app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove imports
content = re.sub(r'from apscheduler.schedulers.asyncio import AsyncIOScheduler\n', '', content)
content = re.sub(r'from apscheduler.triggers.cron import CronTrigger\n', '', content)

# Remove settings Phase 3
content = re.sub(r'    # Phase 3 Settings\n.*?    motion_cooldown_seconds: int = env_int\("MOTION_COOLDOWN_SECONDS", 60, 10, 3600\)\n', '', content, flags=re.DOTALL)

# Remove CameraManager fields
content = re.sub(r'        self.scheduler = AsyncIOScheduler\(\)\n        self.scheduler.start\(\)\n', '', content)
content = re.sub(r'        self.privacy_mode = False\n', '', content)
content = re.sub(r'        self.clip_buffer = deque\(maxlen=max\(1, self._config.fps \* 30\)\)\n        self.motion_enabled = False\n        self._last_motion_time = 0.0\n        self._prev_frame_gray = None\n', '', content)

# Remove CameraManager status dict items
content = re.sub(r'                "privacy_mode": self.privacy_mode,\n', '', content)
content = re.sub(r'                "motion_enabled": self.motion_enabled,\n', '', content)

# Update _capture_loop locals
content = re.sub(r'                    privacy = self.privacy_mode\n', '', content)
content = re.sub(r'                    motion = self.motion_enabled\n', '', content)

# Update _process_frame call
content = re.sub(r'frame = self._process_frame\(cv2, frame, privacy, active_filter, ptz, motion\)', 'frame = self._process_frame(cv2, frame, active_filter, ptz)', content)
content = re.sub(r'                self.clip_buffer.append\(frame.copy\(\) if frame is not None else None\)\n', '', content)

# Update _process_frame signature & body
# We replace the def _process_frame up to the return frame
def_process = r'    def _process_frame\(self, cv2: Any, frame: Any, privacy: bool, filter_mode: str, ptz: dict, motion: bool\) -> Any:\n        if privacy:\n            frame = cv2.blur\(frame, \(50, 50\)\)\n            cv2.putText\(frame, "PRIVACY MODE", \(50, 50\), cv2.FONT_HERSHEY_SIMPLEX, 1, \(255, 255, 255\), 2\)\n            return frame\n            \n        frame = self._enhance_low_light\(cv2, frame\)\n'
new_def_process = '    def _process_frame(self, cv2: Any, frame: Any, filter_mode: str, ptz: dict) -> Any:\n        frame = self._enhance_low_light(cv2, frame)\n'
content = re.sub(def_process, new_def_process, content)

# Remove motion detection logic from _process_frame
motion_block = r'        # Motion detection\n        if motion:.*?            self._prev_frame_gray = gray\n            \n'
content = re.sub(motion_block, '', content, flags=re.DOTALL)

# Write back
with open('app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Refactor stage 1 complete")
