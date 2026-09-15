"""
Screen Control Python SDK

A Python client for the Screen Control API.
Provides easy-to-use functions for screen capture, mouse/keyboard control,
window management, and game mode.

Usage:
    from screen_control import ScreenControl
    
    # Connect to local server
    sc = ScreenControl()
    
    # Or connect to remote server
    sc = ScreenControl(host="192.168.1.100", port=8745, api_key="your-key")
    
    # Take a screenshot
    sc.screenshot("screen.jpg")
    
    # Click at coordinates
    sc.click(500, 300)
    
    # Type text
    sc.type_text("Hello World")
    
    # OCR
    text = sc.ocr()
    print(text)
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    raise ImportError("requests package required: pip install requests")


class ScreenControlError(Exception):
    """Screen Control API error."""
    pass


class ScreenControl:
    """
    Screen Control API client.
    
    Args:
        host: Server host (default: 127.0.0.1)
        port: Server port (default: 8745)
        api_key: API key or session token
        timeout: Request timeout in seconds (default: 10)
        retries: Number of retries on failure (default: 3)
    """
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8745,
        api_key: Optional[str] = None,
        timeout: int = 10,
        retries: int = 3,
    ):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        
        # Load API key
        if api_key:
            self.api_key = api_key
        else:
            self.api_key = self._load_local_token()
        
        self.session.headers["X-Auth-Token"] = self.api_key
    
    def _load_local_token(self) -> str:
        """Load token from local .token file."""
        token_file = Path(__file__).parent.parent / ".token"
        if token_file.exists():
            return token_file.read_text().strip()
        raise ScreenControlError("No API key provided and no local .token file found")
    
    def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make API request with retry logic."""
        url = urljoin(self.base_url, path)
        kwargs.setdefault("timeout", self.timeout)
        
        for attempt in range(self.retries):
            try:
                resp = self.session.request(method, url, **kwargs)
                
                if resp.status_code == 401:
                    raise ScreenControlError("Unauthorized: invalid API key")
                
                if resp.status_code >= 500 and attempt < self.retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                
                if not resp.ok:
                    try:
                        error = resp.json().get("error", resp.text)
                    except:
                        error = resp.text
                    raise ScreenControlError(f"API error {resp.status_code}: {error}")
                
                return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"ok": True, "data": resp.content}
                
            except requests.exceptions.RequestException as e:
                if attempt < self.retries - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ScreenControlError(f"Request failed: {e}")
        
        raise ScreenControlError("Max retries exceeded")
    
    # --- Info ---
    
    def info(self) -> dict:
        """Get server information."""
        return self._request("GET", "/api/info")
    
    def monitors(self) -> list[dict]:
        """List available monitors."""
        return self._request("GET", "/api/monitors")
    
    # --- Screenshot ---
    
    def screenshot(
        self,
        output: Optional[str] = None,
        monitor: int = 1,
        region: Optional[tuple[int, int, int, int]] = None,
    ) -> bytes:
        """
        Take a screenshot.
        
        Args:
            output: Save to file path (optional)
            monitor: Monitor index (default: 1)
            region: Region tuple (x, y, w, h)
            
        Returns:
            JPEG image bytes
        """
        params = {"monitor": monitor}
        if region:
            params["region"] = f"{region[0]},{region[1]},{region[2]},{region[3]}"
        
        resp = self.session.get(
            f"{self.base_url}/api/screenshot",
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        
        if output:
            Path(output).write_bytes(resp.content)
        
        return resp.content
    
    def vision_frame(
        self,
        scale: float = 1.0,
        gray: bool = False,
        quality: int = 80,
        as_base64: bool = False,
        region: Optional[tuple[int, int, int, int]] = None,
    ) -> dict:
        """
        Get a vision frame.
        
        Args:
            scale: Downscale factor (0.5 = half size)
            gray: Convert to grayscale
            quality: JPEG quality (20-95)
            as_base64: Return as base64 string
            region: Region tuple (x, y, w, h)
            
        Returns:
            Dict with image data
        """
        params = {
            "scale": scale,
            "gray": 1 if gray else 0,
            "quality": quality,
        }
        if as_base64:
            params["format"] = "base64"
        if region:
            params["region"] = f"{region[0]},{region[1]},{region[2]},{region[3]}"
        
        return self._request("GET", "/api/vision/frame", params=params)
    
    # --- Mouse ---
    
    def click(self, x: int, y: int, button: str = "left", clicks: int = 1):
        """Click at coordinates."""
        return self._request("POST", "/api/mouse", json={
            "action": "click", "x": x, "y": y, "button": button, "clicks": clicks
        })
    
    def right_click(self, x: int, y: int):
        """Right-click at coordinates."""
        return self.click(x, y, button="right")
    
    def double_click(self, x: int, y: int):
        """Double-click at coordinates."""
        return self.click(x, y, clicks=2)
    
    def scroll(self, clicks: int, x: Optional[int] = None, y: Optional[int] = None):
        """Scroll mouse wheel."""
        body = {"action": "scroll", "clicks": clicks}
        if x is not None and y is not None:
            body["x"] = x
            body["y"] = y
        return self._request("POST", "/api/mouse", json=body)
    
    def move(self, x: int, y: int):
        """Move mouse to coordinates."""
        return self._request("POST", "/api/mouse", json={"action": "move", "x": x, "y": y})
    
    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left"):
        """Drag from (x1,y1) to (x2,y2)."""
        return self._request("POST", "/api/mouse", json={
            "action": "drag", "x1": x1, "y1": y1, "x": x2, "y": y2, "button": button
        })
    
    # --- Keyboard ---
    
    def press(self, key: str):
        """Press a key."""
        return self._request("POST", "/api/key", json={"action": "press", "key": key})
    
    def hotkey(self, *keys: str):
        """Press key combination (e.g., hotkey('ctrl', 'c'))."""
        return self._request("POST", "/api/key", json={"action": "hotkey", "keys": list(keys)})
    
    def type_text(self, text: str, interval: float = 0.03):
        """Type text (supports Unicode)."""
        return self._request("POST", "/api/key", json={
            "action": "type", "text": text, "interval": interval
        })
    
    def key_down(self, key: str):
        """Hold a key down."""
        return self._request("POST", "/api/key", json={"action": "down", "key": key})
    
    def key_up(self, key: str):
        """Release a held key."""
        return self._request("POST", "/api/key", json={"action": "up", "key": key})
    
    # --- OCR ---
    
    def ocr(self, region: Optional[tuple[int, int, int, int]] = None) -> dict:
        """
        Perform OCR on screen.
        
        Args:
            region: Region tuple (x, y, w, h) for faster processing
            
        Returns:
            Dict with 'text', 'lines', and 'items' (with coordinates)
        """
        body = {}
        if region:
            body["region"] = list(region)
        return self._request("POST", "/api/ocr", json=body)
    
    # --- Window Management ---
    
    def windows(self) -> list[dict]:
        """List all visible windows."""
        return self._request("GET", "/api/windows")["windows"]
    
    def focus_window(self, hwnd: int):
        """Bring window to foreground."""
        return self._request("POST", "/api/window", json={"action": "focus", "hwnd": hwnd})
    
    def close_window(self, hwnd: int, expect_title: Optional[str] = None):
        """Close window safely via WM_CLOSE."""
        body = {"action": "close", "hwnd": hwnd}
        if expect_title:
            body["expect_title"] = expect_title
        return self._request("POST", "/api/window", json=body)
    
    def kill_process(self, hwnd: int, pid: int):
        """Force kill a process."""
        return self._request("POST", "/api/window", json={
            "action": "kill", "hwnd": hwnd, "pid": pid
        })
    
    def capture_window(self, hwnd: int, ocr: bool = False) -> dict:
        """Capture a window (works even when background)."""
        params = {"hwnd": hwnd}
        if ocr:
            params["ocr"] = 1
        return self._request("GET", "/api/window/capture", params=params)
    
    def window_type(self, hwnd: int, text: str):
        """Type into a background window."""
        return self._request("POST", "/api/window/post", json={
            "hwnd": hwnd, "action": "type", "text": text
        })
    
    # --- Game Mode ---
    
    def game_start(self, sensitivity: int = 12):
        """Start game mode (locks cursor to center)."""
        return self._request("POST", "/api/game", json={
            "action": "start", "sensitivity": sensitivity
        })
    
    def game_move(self, dx: int, dy: int, sensitivity: int = 12):
        """Rotate camera in game mode."""
        return self._request("POST", "/api/game", json={
            "action": "move", "dx": dx, "dy": dy, "sensitivity": sensitivity
        })
    
    def game_stop(self):
        """Stop game mode."""
        return self._request("POST", "/api/game", json={"action": "stop"})
    
    def game_heartbeat(self):
        """Send heartbeat to keep game mode alive."""
        return self._request("POST", "/api/game", json={"action": "heartbeat"})
    
    # --- Safety ---
    
    def release_all(self):
        """Emergency: release all held keys/buttons."""
        return self._request("POST", "/api/release_all", json={})
    
    def held_state(self) -> dict:
        """Get currently held keys/buttons."""
        return self._request("GET", "/api/held")
    
    # --- API Keys ---
    
    def create_api_key(self, name: str) -> str:
        """Create a new persistent API key."""
        result = self._request("POST", "/api/keys", json={"action": "create", "name": name})
        return result["key"]
    
    def list_api_keys(self) -> list[dict]:
        """List API key names."""
        return self._request("POST", "/api/keys", json={"action": "list"})
    
    def revoke_api_key(self, name: str):
        """Revoke an API key."""
        return self._request("POST", "/api/keys", json={"action": "revoke", "name": name})


# Convenience alias
SC = ScreenControl