#!/usr/bin/env python3
"""
🍓 Berry Native - Pygame UI for Raspberry Pi
A lightweight music player that connects directly to go-librespot.

Usage:
    python berry.py              # Windowed (development)
    python berry.py --fullscreen # Fullscreen (Pi)
"""

import os
import sys
import json
import time
import threading
import hashlib
import subprocess
from pathlib import Path
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, List, Dict
from io import BytesIO

import pygame
import requests
from PIL import Image
import websocket

# ============================================
# CONFIGURATION
# ============================================

SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720

LIBRESPOT_URL = os.environ.get('LIBRESPOT_URL', 'http://localhost:3678')
LIBRESPOT_WS = os.environ.get('LIBRESPOT_WS', 'ws://localhost:3678/events')
BACKEND_URL = os.environ.get('BACKEND_URL', 'http://localhost:3001')

# Mock mode for UI testing without librespot
MOCK_MODE = '--mock' in sys.argv or '-m' in sys.argv

# Use backend's data folder (shared catalog & images)
DATA_DIR = Path(__file__).parent.parent / 'backend' / 'data'
CATALOG_PATH = DATA_DIR / 'catalog.json'
IMAGES_DIR = DATA_DIR / 'images'

# Design specs from web version (App.css)
COLORS = {
    'bg_primary': (13, 13, 13),
    'bg_secondary': (26, 26, 26),
    'bg_elevated': (40, 40, 40),
    'accent': (189, 101, 252),  # Purple #BD65FC
    'accent_hover': (205, 130, 255),
    'text_primary': (255, 255, 255),
    'text_secondary': (160, 160, 160),
    'text_muted': (96, 96, 96),
    'success': (29, 185, 84),
    'error': (232, 80, 80),
}

# Sizes - Larger covers for more impact
COVER_SIZE = 410
COVER_SIZE_SMALL = int(COVER_SIZE * 0.75)  # 300
COVER_SPACING = 20

# Layout - Track info ABOVE carousel, controls at bottom
TRACK_INFO_Y = 45    # Track name near top
CAROUSEL_Y = 120     # Below track info
CONTROLS_Y = 620     # Bottom area

# Button sizes - Larger for easier touch
BTN_SIZE = 100
PLAY_BTN_SIZE = 120

# Progress bar (inside cover, at bottom edge)
PROGRESS_BAR_HEIGHT = 8

# Volume levels (toggle between these)
VOLUME_LEVELS = [
    {'level': 50, 'icon': 'volume_none'},
    {'level': 75, 'icon': 'volume_low'},
    {'level': 100, 'icon': 'volume_high'},
]

# ============================================
# DATA CLASSES
# ============================================

@dataclass
class CatalogItem:
    id: str
    uri: str
    name: str
    type: str = 'album'
    artist: Optional[str] = None
    image: Optional[str] = None
    images: Optional[List[str]] = None
    current_track: Optional[dict] = None
    is_temp: bool = False

@dataclass
class NowPlaying:
    playing: bool = False
    paused: bool = False
    stopped: bool = True
    context_uri: Optional[str] = None
    track_name: Optional[str] = None
    track_artist: Optional[str] = None
    track_album: Optional[str] = None
    track_cover: Optional[str] = None
    position: int = 0
    duration: int = 0

# ============================================
# TOUCH HANDLER
# ============================================

class TouchHandler:
    """Handle swipe gestures for carousel navigation."""
    
    SWIPE_THRESHOLD = 50      # Minimum distance for swipe
    SWIPE_VELOCITY = 0.3      # Minimum velocity (pixels/ms)
    LONG_PRESS_TIME = 1.0     # Time for long press (seconds)
    
    def __init__(self):
        self.start_x = 0
        self.start_y = 0
        self.start_time = 0
        self.dragging = False
        self.drag_offset = 0  # Current drag offset in pixels
        self.long_press_fired = False  # Track if long press was triggered
    
    def on_down(self, pos):
        """Called on touch/mouse down."""
        self.start_x = pos[0]
        self.start_y = pos[1]
        self.start_time = time.time()
        self.dragging = True
        self.drag_offset = 0
        self.long_press_fired = False
    
    def on_move(self, pos) -> float:
        """Called on touch/mouse move. Returns drag offset."""
        if not self.dragging:
            return 0
        self.drag_offset = pos[0] - self.start_x
        return self.drag_offset
    
    def check_long_press(self) -> bool:
        """Check if long press threshold reached. Returns True once."""
        if not self.dragging or self.long_press_fired:
            return False
        
        # Only trigger if finger hasn't moved much
        if abs(self.drag_offset) > 20:
            return False
        
        if time.time() - self.start_time >= self.LONG_PRESS_TIME:
            self.long_press_fired = True
            return True
        
        return False
    
    def on_up(self, pos) -> tuple:
        """
        Called on touch/mouse up.
        Returns (action, velocity) where action is 'left', 'right', 'tap', or None.
        Velocity is in pixels/ms (positive = right, negative = left).
        """
        if not self.dragging:
            return (None, 0)
        
        self.dragging = False
        dx = pos[0] - self.start_x
        dy = pos[1] - self.start_y
        dt = (time.time() - self.start_time) * 1000  # ms
        
        # Ignore if mostly vertical
        if abs(dy) > abs(dx) * 1.5:
            self.drag_offset = 0
            return ('tap', 0)
        
        # Use minimum dt of 50ms to prevent extreme velocity on instant release
        dt_clamped = max(50, dt)
        velocity = dx / dt_clamped if dt_clamped > 0 else 0
        
        # Also cap velocity to reasonable range (-5 to 5 px/ms)
        velocity = max(-5.0, min(5.0, velocity))
        
        # Check for swipe
        if abs(dx) >= self.SWIPE_THRESHOLD or abs(velocity) >= self.SWIPE_VELOCITY:
            self.drag_offset = 0
            action = 'right' if dx > 0 else 'left'
            return (action, velocity)
        
        self.drag_offset = 0
        return ('tap', 0)

# ============================================
# PLAY TIMER
# ============================================

class PlayTimer:
    """Auto-play after settling on a cover for 1 second."""
    
    DELAY = 1.0  # seconds
    SYNC_COOLDOWN = 3.0  # Block sync for 3s after firing
    
    def __init__(self):
        self.item = None
        self.start_time = 0
        self.last_played_uri = None  # Track what we just played
        self.last_fired_time = 0     # Track when we last fired (for sync cooldown)
    
    def start(self, item):
        """Start timer for an item."""
        if item is None:
            self.cancel()
            return
        
        # Don't restart if same item
        if self.item and self.item.uri == item.uri:
            return
        
        self.item = item
        self.start_time = time.time()
    
    def cancel(self):
        """Cancel the timer."""
        self.item = None
        self.start_time = 0
    
    def check(self) -> Optional[CatalogItem]:
        """Check if timer expired. Returns item to play or None."""
        if not self.item:
            return None
        
        if time.time() - self.start_time >= self.DELAY:
            result = self.item
            self.last_played_uri = result.uri
            self.last_fired_time = time.time()  # Track when we fired
            self.item = None
            self.start_time = 0
            return result
        
        return None

# ============================================
# SMOOTH SCROLL CAROUSEL
# ============================================

class SmoothCarousel:
    """Smooth scrolling carousel - items follow finger, then lerp to target."""
    
    LERP_SPEED = 0.25          # Animation speed (0-1, higher = faster)
    SNAP_THRESHOLD = 0.01      # When to finish animation
    
    def __init__(self):
        self.scroll_x = 0.0         # Current scroll position (float index)
        self.target_index = 0       # Target index to animate to
        self.settled = True         # True when not animating
        self.max_index = 0          # Will be set by app
    
    def set_target(self, index: int):
        """Set target index to animate to."""
        self.target_index = max(0, min(index, self.max_index))
        self.settled = False
    
    def update(self, dt: float) -> bool:
        """Update scroll position. Returns True if position changed."""
        if self.settled:
            return False
        
        # Lerp to target
        target = float(self.target_index)
        diff = target - self.scroll_x
        
        self.scroll_x += diff * self.LERP_SPEED
        
        # Check if settled
        if abs(diff) < self.SNAP_THRESHOLD:
            self.scroll_x = target
            self.settled = True
        
        return True
    
    def get_offset(self, item_index: int) -> float:
        """Get the x offset for an item (used for drawing)."""
        return item_index - self.scroll_x

# ============================================
# SLEEP MANAGER
# ============================================

class SleepManager:
    """Manages deep sleep mode for power saving and screen burn-in prevention."""
    
    SLEEP_TIMEOUT = 120.0  # 2 minutes of inactivity
    BACKLIGHT_DIR = '/sys/class/backlight'
    
    def __init__(self):
        self.is_sleeping = False
        self.last_activity = time.time()
        self.backlight_path = self._detect_backlight()
        if self.backlight_path:
            print(f'💡 Backlight detected: {self.backlight_path}')
        else:
            print('💡 No backlight found (not on Pi?)')
    
    def _detect_backlight(self) -> Optional[str]:
        """Detect the correct backlight path for any Pi touchscreen."""
        try:
            backlights = os.listdir(self.BACKLIGHT_DIR)
            if backlights:
                # Use the first available backlight (works for rpi_backlight, 10-0045, etc.)
                return f'{self.BACKLIGHT_DIR}/{backlights[0]}/bl_power'
        except Exception:
            pass
        return None
    
    def reset_timer(self):
        """Reset the sleep timer (called on user activity or playback)."""
        self.last_activity = time.time()
        if self.is_sleeping:
            self.wake_up()
    
    def check_sleep(self, is_playing: bool) -> bool:
        """Check if should enter sleep mode. Returns True if sleeping."""
        if self.is_sleeping:
            return True
        
        # Don't sleep if music is playing
        if is_playing:
            self.last_activity = time.time()
            return False
        
        # Check timeout
        if time.time() - self.last_activity >= self.SLEEP_TIMEOUT:
            self.enter_sleep()
            return True
        
        return False
    
    def enter_sleep(self):
        """Enter deep sleep mode - turn off backlight."""
        if self.is_sleeping:
            return
        
        print('💤 Entering sleep mode...')
        self.is_sleeping = True
        self._set_backlight(False)
    
    def wake_up(self):
        """Wake from sleep mode - turn on backlight."""
        if not self.is_sleeping:
            return
        
        print('☀️ Waking up...')
        self.is_sleeping = False
        self.last_activity = time.time()
        self._set_backlight(True)
    
    def _set_backlight(self, on: bool):
        """Control the Raspberry Pi backlight."""
        if not self.backlight_path:
            return
        
        try:
            # 0 = on, 1 = off (inverted logic)
            value = '0' if on else '1'
            with open(self.backlight_path, 'w') as f:
                f.write(value)
            print(f'  📺 Backlight {"on" if on else "off"}')
        except Exception as e:
            # Not running on Pi or no permission
            print(f'  ⚠️ Could not control backlight: {e}')

# ============================================
# LIBRESPOT API CLIENT
# ============================================

class LibrespotAPI:
    """Direct REST API client for go-librespot."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers['Content-Type'] = 'application/json'
    
    def status(self) -> Optional[dict]:
        """Get current playback status."""
        try:
            resp = self.session.get(f'{self.base_url}/status', timeout=2)
            if resp.status_code == 204:
                return None
            return resp.json()
        except:
            return None
    
    def play(self, uri: str, skip_to_uri: str = None) -> bool:
        """Play a Spotify URI (album/playlist), optionally starting at a specific track."""
        try:
            body = {'uri': uri}
            if skip_to_uri:
                body['skip_to_uri'] = skip_to_uri
                print(f'  📍 Resuming at track: {skip_to_uri}')
            
            resp = self.session.post(
                f'{self.base_url}/player/play',
                json=body,
                timeout=5
            )
            if resp.ok:
                print(f'  ✅ Play request sent')
            else:
                print(f'  ❌ Play failed: {resp.status_code} {resp.text}')
            return resp.ok
        except Exception as e:
            print(f'  ❌ Play error: {e}')
            return False
    
    def pause(self) -> bool:
        try:
            resp = self.session.post(f'{self.base_url}/player/pause', timeout=2)
            print(f'  ⏸ Pause: {resp.status_code}')
            return resp.ok
        except Exception as e:
            print(f'  ❌ Pause error: {e}')
            return False
    
    def resume(self) -> bool:
        try:
            resp = self.session.post(f'{self.base_url}/player/resume', timeout=2)
            print(f'  ▶️ Resume: {resp.status_code}')
            return resp.ok
        except Exception as e:
            print(f'  ❌ Resume error: {e}')
            return False
    
    def next(self) -> bool:
        try:
            resp = self.session.post(f'{self.base_url}/player/next', timeout=2)
            print(f'  ⏭ Next: {resp.status_code}')
            return resp.ok
        except Exception as e:
            print(f'  ❌ Next error: {e}')
            return False
    
    def prev(self) -> bool:
        try:
            resp = self.session.post(f'{self.base_url}/player/prev', timeout=2)
            print(f'  ⏮ Prev: {resp.status_code}')
            return resp.ok
        except Exception as e:
            print(f'  ❌ Prev error: {e}')
            return False
    
    def is_connected(self) -> bool:
        """Check if librespot is reachable."""
        try:
            resp = self.session.get(f'{self.base_url}/status', timeout=1)
            return resp.status_code in (200, 204)
        except:
            return False
    
    def set_volume(self, level: int) -> bool:
        """Set volume level (0-100)."""
        try:
            resp = self.session.post(
                f'{self.base_url}/player/volume',
                json={'volume': level},
                timeout=2
            )
            print(f'  🔊 Volume: {level}% ({resp.status_code})')
            return resp.ok
        except Exception as e:
            print(f'  ❌ Volume error: {e}')
            return False
    
    def save_to_catalog(self, item: dict) -> bool:
        """Save item to catalog via backend."""
        try:
            resp = self.session.post(
                f'{BACKEND_URL}/api/catalog',
                json=item,
                timeout=10
            )
            if resp.ok:
                print(f'  ✅ Saved to catalog')
            else:
                print(f'  ❌ Save failed: {resp.status_code} {resp.text}')
            return resp.ok
        except Exception as e:
            print(f'  ❌ Save error: {e}')
            return False
    
    def delete_from_catalog(self, item_id: str) -> bool:
        """Delete item from catalog via backend."""
        try:
            resp = self.session.delete(
                f'{BACKEND_URL}/api/catalog/{item_id}',
                timeout=5
            )
            if resp.ok:
                print(f'  ✅ Deleted from catalog')
            else:
                print(f'  ❌ Delete failed: {resp.status_code} {resp.text}')
            return resp.ok
        except Exception as e:
            print(f'  ❌ Delete error: {e}')
            return False

# ============================================
# WEBSOCKET EVENT LISTENER
# ============================================

class EventListener:
    """Listens to go-librespot WebSocket events."""
    
    def __init__(self, url: str, on_update):
        self.url = url
        self.on_update = on_update
        self.ws = None
        self.thread = None
        self.running = False
        self.context_uri = None
    
    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
    
    def stop(self):
        self.running = False
        if self.ws:
            self.ws.close()
    
    def _run(self):
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    self.url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.ws.run_forever()
            except Exception as e:
                print(f'WebSocket error: {e}')
            
            if self.running:
                time.sleep(3)
    
    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            event_type = data.get('type')
            
            if event_type == 'playing':
                self.context_uri = data.get('data', {}).get('context_uri')
            
            # Notify app to refresh state
            self.on_update()
        except Exception as e:
            print(f'Error parsing event: {e}')
    
    def _on_error(self, ws, error):
        pass  # Suppress errors
    
    def _on_close(self, ws, close_status, close_msg):
        pass

# ============================================
# CATALOG MANAGER
# ============================================

class Catalog:
    """Manages saved albums/playlists."""
    
    def __init__(self, path: Path, mock_mode: bool = False):
        self.path = path
        self.mock_mode = mock_mode
        self.items: List[CatalogItem] = []
    
    def load(self):
        if self.mock_mode:
            self._load_mock_data()
            return
        
        try:
            print(f'📂 Loading catalog from {self.path}')
            if self.path.exists():
                data = json.loads(self.path.read_text())
                items_data = data.get('items', []) if isinstance(data, dict) else []
                self.items = [
                    CatalogItem(
                        id=item.get('id', ''),
                        uri=item.get('uri', ''),
                        name=item.get('name', ''),
                        type=item.get('type', 'album'),
                        artist=item.get('artist'),
                        image=item.get('image'),
                        images=item.get('images'),
                        current_track=item.get('currentTrack'),
                    )
                    for item in items_data
                    if isinstance(item, dict) and item.get('type') != 'track'
                ]
                print(f'✅ Loaded {len(self.items)} items')
            else:
                print(f'⚠️ Catalog not found at {self.path}')
                self.items = []
        except Exception as e:
            print(f'❌ Error loading catalog: {e}')
            import traceback
            traceback.print_exc()
            self.items = []
    
    def _load_mock_data(self):
        """Load mock data for UI testing."""
        self.items = [
            CatalogItem(
                id='1', uri='spotify:album:mock1',
                name='Abbey Road', type='album',
                artist='The Beatles',
                image='https://i.scdn.co/image/ab67616d0000b273dc30583ba717007b00cceb25',
                current_track={'name': 'Come Together', 'artist': 'The Beatles'}
            ),
            CatalogItem(
                id='2', uri='spotify:album:mock2',
                name='Dark Side of the Moon', type='album',
                artist='Pink Floyd',
                image='https://i.scdn.co/image/ab67616d0000b273ea7caaff71dea1051d49b2fe',
            ),
            CatalogItem(
                id='3', uri='spotify:album:mock3',
                name='Rumours', type='album',
                artist='Fleetwood Mac',
                image='https://i.scdn.co/image/ab67616d0000b273e52a59a28efa4773dd2bfe1b',
            ),
            CatalogItem(
                id='4', uri='spotify:album:mock4',
                name='Back in Black', type='album',
                artist='AC/DC',
                image='https://i.scdn.co/image/ab67616d0000b2734809adfae9bd679cffadd3a3',
            ),
            CatalogItem(
                id='5', uri='spotify:album:mock5',
                name='Thriller', type='album',
                artist='Michael Jackson',
                image='https://i.scdn.co/image/ab67616d0000b27334bfb69e00898660fc3c3ab3',
            ),
        ]
    
    def save(self):
        if self.mock_mode:
            return
        data = {
            'items': [
                {
                    'id': item.id,
                    'uri': item.uri,
                    'name': item.name,
                    'type': item.type,
                    'artist': item.artist,
                    'image': item.image,
                    'images': item.images,
                    'currentTrack': item.current_track,
                }
                for item in self.items
                if not item.is_temp
            ]
        }
        self.path.write_text(json.dumps(data, indent=2))

# ============================================
# CATALOG MANAGER (Direct operations without backend)
# ============================================

class CatalogManager:
    """
    Direct catalog operations without Node.js backend.
    Handles save/delete, image dedup, progress tracking, and playlist covers.
    """
    
    PROGRESS_EXPIRY_HOURS = 24
    
    def __init__(self, catalog_path: Path, images_path: Path):
        self.catalog_path = catalog_path
        self.images_path = images_path
        self.images_path.mkdir(parents=True, exist_ok=True)
        
        # Hash -> local_path for deduplication
        self.image_hashes: Dict[str, str] = {}
        
        # Playlist covers collection: {context_uri: {hash: local_path}}
        self.playlist_covers: Dict[str, Dict[str, str]] = {}
        
        # Index existing images on startup
        self._index_existing_images()
    
    def _index_existing_images(self):
        """Index existing images by extracting hash from filename."""
        try:
            for file in self.images_path.iterdir():
                if not file.suffix == '.jpg':
                    continue
                # Extract hash from filename: "1767089701460-6aa1f146.jpg" -> "6aa1f146"
                match = file.name.split('-')
                if len(match) >= 2:
                    hash_part = match[-1].replace('.jpg', '')
                    if len(hash_part) == 8:  # Valid 8-char hash
                        self.image_hashes[hash_part] = f'/images/{file.name}'
            
            print(f'📁 Indexed {len(self.image_hashes)} images')
        except Exception as e:
            print(f'⚠️ Error indexing images: {e}')
    
    def _load_catalog(self) -> dict:
        """Load catalog.json."""
        try:
            if self.catalog_path.exists():
                return json.loads(self.catalog_path.read_text())
            return {'items': []}
        except Exception:
            return {'items': []}
    
    def _save_catalog(self, catalog: dict):
        """Save catalog.json."""
        self.catalog_path.write_text(json.dumps(catalog, indent=2))
    
    def _download_and_hash_image(self, image_url: str) -> tuple:
        """Download image and return (hash, buffer)."""
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        buffer = response.content
        hash_full = hashlib.md5(buffer).hexdigest()
        hash_short = hash_full[:8]  # Use first 8 chars like backend
        return (hash_short, buffer)
    
    def _save_image(self, hash_short: str, buffer: bytes) -> str:
        """Save image to disk and return local path."""
        # Check if already exists
        if hash_short in self.image_hashes:
            return self.image_hashes[hash_short]
        
        # Save new image
        filename = f'{int(time.time() * 1000)}-{hash_short}.jpg'
        filepath = self.images_path / filename
        filepath.write_bytes(buffer)
        
        local_path = f'/images/{filename}'
        self.image_hashes[hash_short] = local_path
        print(f'💾 Saved new image: {local_path}')
        return local_path
    
    # ---- Playlist Cover Collection ----
    
    def collect_cover_for_playlist(self, context_uri: str, cover_url: str):
        """Collect album cover for playlist composite (max 4 unique)."""
        if 'playlist' not in context_uri or not cover_url:
            return
        
        if context_uri not in self.playlist_covers:
            self.playlist_covers[context_uri] = {}
        
        covers = self.playlist_covers[context_uri]
        if len(covers) >= 4:
            return  # Already have 4 covers
        
        # Skip if we've already tried this URL recently (avoid repeated downloads)
        if not hasattr(self, '_tried_cover_urls'):
            self._tried_cover_urls = set()
        
        url_key = f'{context_uri}:{cover_url}'
        if url_key in self._tried_cover_urls:
            return
        self._tried_cover_urls.add(url_key)
        
        try:
            hash_short, buffer = self._download_and_hash_image(cover_url)
            
            # Skip if already have this hash for this context
            if hash_short in covers:
                print(f'📸 Cover already collected (same album): {len(covers)}/4')
                return
            
            # Save/reuse image
            local_path = self._save_image(hash_short, buffer)
            covers[hash_short] = local_path
            print(f'📸 NEW cover collected: {len(covers)}/4 for playlist')
            
            # Update catalog if this playlist is already saved
            self._update_playlist_covers_if_needed(context_uri, local_path)
            
        except Exception as e:
            print(f'⚠️ Error collecting cover: {e}')
    
    def _update_playlist_covers_if_needed(self, context_uri: str, local_path: str):
        """Update saved playlist with new covers progressively."""
        try:
            catalog = self._load_catalog()
            item = next((i for i in catalog['items'] if i['uri'] == context_uri), None)
            
            if not item or item.get('type') != 'playlist':
                return
            
            current_images = item.get('images') or []
            if len(current_images) >= 4:
                return
            if local_path in current_images:
                return
            
            item['images'] = current_images + [local_path]
            self._save_catalog(catalog)
            print(f'📸 Updated saved playlist cover {len(item["images"])}/4')
        except Exception as e:
            print(f'⚠️ Error updating playlist covers: {e}')
    
    # ---- Save/Delete ----
    
    def save_item(self, item_data: dict) -> bool:
        """Save item to catalog with image download and deduplication."""
        try:
            catalog = self._load_catalog()
            
            # Check for duplicates
            uri = item_data.get('uri')
            if any(i['uri'] == uri for i in catalog['items']):
                print(f'⚠️ Item already in catalog: {item_data.get("name")}')
                return False
            
            local_image = None
            local_images = None
            
            # For playlists: use collected covers if available
            if item_data.get('type') == 'playlist' and uri in self.playlist_covers:
                covers = list(self.playlist_covers[uri].values())
                if covers:
                    local_images = covers
                    local_image = covers[0]
                    print(f'💾 Using {len(covers)} pre-collected covers for playlist')
            
            # Download single image if no composite (albums)
            image_url = item_data.get('image')
            if not local_image and image_url and image_url.startswith('http'):
                try:
                    hash_short, buffer = self._download_and_hash_image(image_url)
                    local_image = self._save_image(hash_short, buffer)
                except Exception as e:
                    print(f'⚠️ Error downloading image: {e}')
                    local_image = image_url  # Fallback to URL
            
            # Build new item
            new_item = {
                'id': str(int(time.time() * 1000)),
                'type': item_data.get('type', 'album'),
                'uri': uri,
                'name': item_data.get('name'),
                'artist': item_data.get('artist'),
                'album': item_data.get('album'),
                'image': local_image or item_data.get('image'),
                'images': local_images,
                'originalImage': item_data.get('image'),
                'addedAt': datetime.now().isoformat(),
            }
            
            catalog['items'].append(new_item)
            self._save_catalog(catalog)
            print(f'✅ Saved to catalog: {new_item["name"]}')
            return True
            
        except Exception as e:
            print(f'❌ Error saving to catalog: {e}')
            return False
    
    def delete_item(self, item_id: str) -> bool:
        """Delete item from catalog."""
        try:
            catalog = self._load_catalog()
            
            index = next((i for i, item in enumerate(catalog['items']) 
                         if item['id'] == item_id), None)
            if index is None:
                print(f'⚠️ Item not found: {item_id}')
                return False
            
            removed = catalog['items'].pop(index)
            self._save_catalog(catalog)
            print(f'🗑️ Deleted from catalog: {removed.get("name")}')
            return True
            
        except Exception as e:
            print(f'❌ Error deleting from catalog: {e}')
            return False
    
    # ---- Progress Tracking ----
    
    def save_progress(self, context_uri: str, track_uri: str, 
                      position: int, track_name: str = None, artist: str = None):
        """Save playback progress to catalog item."""
        if not context_uri or not track_uri:
            print(f'⚠️ save_progress: missing context_uri or track_uri')
            return
        
        try:
            catalog = self._load_catalog()
            item = next((i for i in catalog['items'] if i['uri'] == context_uri), None)
            
            if not item:
                print(f'💾 save_progress: context not in catalog (tempItem?): {context_uri[:40]}...')
                return
            
            item['currentTrack'] = {
                'uri': track_uri,
                'position': position,
                'name': track_name,
                'artist': artist,
                'updatedAt': datetime.now().isoformat()
            }
            self._save_catalog(catalog)
            print(f'💾 Saved: {track_name} @ {position // 1000}s')
            
        except Exception as e:
            print(f'⚠️ Error saving progress: {e}')
    
    def get_progress(self, context_uri: str) -> Optional[dict]:
        """Get saved progress if < 24 hours old."""
        try:
            catalog = self._load_catalog()
            
            # Debug: show what we're looking for
            print(f'📍 Looking for progress: {context_uri}')
            
            item = next((i for i in catalog['items'] if i['uri'] == context_uri), None)
            
            if not item:
                print(f'📍 Not in catalog')
                return None
            
            print(f'📍 Found catalog item: {item.get("name")}')
            
            if 'currentTrack' not in item:
                print(f'📍 No currentTrack saved')
                return None
            
            current_track = item['currentTrack']
            
            # Check age
            updated_at = current_track.get('updatedAt')
            if updated_at:
                updated = datetime.fromisoformat(updated_at)
                age_hours = (datetime.now() - updated).total_seconds() / 3600
                if age_hours > self.PROGRESS_EXPIRY_HOURS:
                    print(f'📍 Progress expired ({age_hours:.1f}h old)')
                    self.clear_progress(context_uri)
                    return None
            
            print(f'📍 Resume: "{current_track.get("name")}" (track: {current_track.get("uri", "?")[:40]}...) @ {current_track.get("position", 0) // 1000}s')
            return current_track
            
        except Exception as e:
            print(f'⚠️ Error getting progress: {e}')
            return None
    
    def clear_progress(self, context_uri: str):
        """Clear saved progress for a context."""
        if not context_uri:
            return
        
        try:
            catalog = self._load_catalog()
            item = next((i for i in catalog['items'] if i['uri'] == context_uri), None)
            
            if item and 'currentTrack' in item:
                del item['currentTrack']
                self._save_catalog(catalog)
                print(f'🗑️ Cleared progress for: {item.get("name")}')
                
        except Exception as e:
            print(f'⚠️ Error clearing progress: {e}')
    
    # ---- Image Garbage Collection ----
    
    def cleanup_unused_images(self) -> int:
        """Delete images not referenced in catalog. Returns count deleted."""
        try:
            catalog = self._load_catalog()
            
            # Collect all used images
            used = set()
            for item in catalog['items']:
                if item.get('image', '').startswith('/images/'):
                    used.add(item['image'].replace('/images/', ''))
                for img in item.get('images') or []:
                    if img and img.startswith('/images/'):
                        used.add(img.replace('/images/', ''))
            
            # Find and delete unused
            deleted = 0
            for file in self.images_path.iterdir():
                if file.name not in used and file.suffix == '.jpg':
                    file.unlink()
                    deleted += 1
                    # Remove from hash index
                    self.image_hashes = {h: p for h, p in self.image_hashes.items()
                                         if p != f'/images/{file.name}'}
            
            if deleted:
                print(f'🧹 Cleanup: {deleted} unused images deleted')
            return deleted
            
        except Exception as e:
            print(f'⚠️ Error cleaning up images: {e}')
            return 0

# ============================================
# IMAGE CACHE
# ============================================

class ImageCache:
    """Downloads and caches album cover images with pre-loading support."""
    
    # Maximum cache size to prevent memory issues
    MAX_CACHE_SIZE = 200
    
    def __init__(self, images_dir: Path):
        self.images_dir = images_dir
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.cache: dict[str, pygame.Surface] = {}
        self.loading: set[str] = set()
        self.placeholder: Optional[pygame.Surface] = None
        self._preload_queue: List[tuple] = []  # [(image_path, size), ...]
        self._preload_lock = threading.Lock()
        self._preloading = False
    
    def get_placeholder(self, size: int) -> pygame.Surface:
        cache_key = f'_placeholder_{size}'
        if cache_key not in self.cache:
            placeholder = pygame.Surface((size, size), pygame.SRCALPHA)
            radius = max(12, size // 25)
            # Just a plain gray rounded rect - no text (avoids font rendering issues)
            draw_aa_rounded_rect(placeholder, COLORS['bg_elevated'], 
                                (0, 0, size, size), radius)
            self.cache[cache_key] = placeholder.convert_alpha()
        return self.cache[cache_key]
    
    def preload_catalog(self, items: List, sizes: List[int] = None):
        """Pre-load all catalog images in background thread for smooth scrolling."""
        if sizes is None:
            sizes = [COVER_SIZE, COVER_SIZE_SMALL]
        
        with self._preload_lock:
            self._preload_queue.clear()
            for item in items:
                # Add both sizes to queue
                for size in sizes:
                    if item.image:
                        self._preload_queue.append((item.image, size, False))  # Normal
                        self._preload_queue.append((item.image, size, True))   # Dimmed
                    # Add composite images for playlists
                    if item.images:
                        for img in item.images:
                            if img:
                                self._preload_queue.append((img, size // 2, False))
        
        # Start preload thread if not running
        if not self._preloading:
            self._preloading = True
            thread = threading.Thread(target=self._preload_worker, daemon=True)
            thread.start()
            print(f'🖼️ Pre-loading {len(self._preload_queue)} images...')
    
    def _preload_worker(self):
        """Background worker to preload images."""
        loaded = 0
        while True:
            with self._preload_lock:
                if not self._preload_queue:
                    self._preloading = False
                    print(f'✅ Pre-loaded {loaded} images')
                    return
                image_path, size, dimmed = self._preload_queue.pop(0)
            
            # Load into cache (will be reused by get/get_dimmed)
            try:
                if dimmed:
                    self.get_dimmed(image_path, size)
                else:
                    self.get(image_path, size)
                loaded += 1
            except Exception:
                pass
            
            # Small delay to not block main thread
            time.sleep(0.01)
    
    def _evict_if_needed(self):
        """Evict old cache entries if cache is too large."""
        if len(self.cache) > self.MAX_CACHE_SIZE:
            # Remove oldest entries (simple FIFO, could be LRU)
            keys_to_remove = list(self.cache.keys())[:len(self.cache) - self.MAX_CACHE_SIZE + 20]
            for key in keys_to_remove:
                if not key.startswith('_'):  # Keep placeholders
                    del self.cache[key]
            print(f'🧹 Evicted {len(keys_to_remove)} cached images')
    
    def get(self, image_path: Optional[str], size: int = COVER_SIZE) -> pygame.Surface:
        if not image_path:
            return self.get_placeholder(size)
        
        cache_key = f'{image_path}_{size}'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Evict old entries if cache is getting too large
        self._evict_if_needed()
        
        # Try to load from local file
        if image_path.startswith('/images/'):
            local_path = self.images_dir / image_path.replace('/images/', '')
            if local_path.exists():
                return self._load_local(local_path, size, cache_key)
        
        # Try to load from URL
        if image_path.startswith('http'):
            if image_path not in self.loading:
                self.loading.add(image_path)
                thread = threading.Thread(
                    target=self._download,
                    args=(image_path, size, cache_key),
                    daemon=True
                )
                thread.start()
        
        return self.get_placeholder(size)
    
    def get_dimmed(self, image_path: Optional[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get a pre-cached dimmed version of the image (for non-selected items)."""
        if not image_path:
            return self.get_placeholder(size)
        
        cache_key = f'{image_path}_{size}_dimmed'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Get the regular version first
        regular = self.get(image_path, size)
        regular_key = f'{image_path}_{size}'
        
        # Only create dimmed if we have the real image (not placeholder)
        if regular_key in self.cache:
            # Create dimmed version with alpha baked in (140/255 ≈ 55% opacity)
            # Use a dark overlay instead of alpha for better performance
            result = pygame.Surface((size, size), pygame.SRCALPHA)
            result.blit(regular, (0, 0))
            # Apply dark overlay to simulate dimming
            overlay = pygame.Surface((size, size), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 115))  # 45% dark overlay
            result.blit(overlay, (0, 0))
            result = result.convert_alpha()
            self.cache[cache_key] = result
            return result
        
        return regular
    
    def get_composite(self, images: List[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get a 2x2 composite cover for playlists (like web version)."""
        if not images:
            return self.get_placeholder(size)
        
        # Create cache key from all images (use tuple for efficiency)
        images_key = tuple(images[:4]) if images else ()
        cache_key = f'composite_{hash(images_key)}_{size}'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Evict if needed
        self._evict_if_needed()
        
        # Create composite surface
        composite = pygame.Surface((size, size), pygame.SRCALPHA)
        half_size = size // 2
        
        # Positions for 2x2 grid
        positions = [(0, 0), (half_size, 0), (0, half_size), (half_size, half_size)]
        
        for i, pos in enumerate(positions):
            if i < len(images) and images[i]:
                # Get the sub-image (no rounded corners for inner pieces)
                sub_img = self._get_raw(images[i], half_size)
                if sub_img:
                    composite.blit(sub_img, pos)
            else:
                # Fill with placeholder color
                pygame.draw.rect(composite, COLORS['bg_elevated'], 
                               (*pos, half_size, half_size))
        
        # Apply rounded corners to the whole composite
        radius = max(12, size // 25)
        composite = apply_rounded_corners(composite, radius)
        composite = composite.convert_alpha()
        
        self.cache[cache_key] = composite
        return composite
    
    def get_composite_dimmed(self, images: List[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get a dimmed composite cover for playlists (non-selected items)."""
        if not images:
            return self.get_placeholder(size)
        
        images_key = tuple(images[:4]) if images else ()
        cache_key = f'composite_dimmed_{hash(images_key)}_{size}'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Get the regular composite first
        regular = self.get_composite(images, size)
        regular_key = f'composite_{hash(images_key)}_{size}'
        
        # Only create dimmed if we have the real composite
        if regular_key in self.cache:
            result = pygame.Surface((size, size), pygame.SRCALPHA)
            result.blit(regular, (0, 0))
            overlay = pygame.Surface((size, size), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 115))
            result.blit(overlay, (0, 0))
            result = result.convert_alpha()
            self.cache[cache_key] = result
            return result
        
        return regular
    
    def _get_raw(self, image_path: str, size: int) -> Optional[pygame.Surface]:
        """Get image without rounded corners (for composite pieces)."""
        cache_key = f'{image_path}_{size}_raw'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Try local file
        if image_path.startswith('/images/'):
            local_path = self.images_dir / image_path.replace('/images/', '')
            if local_path.exists():
                try:
                    img = Image.open(local_path)
                    img = img.convert('RGBA')
                    img = img.resize((size, size), Image.Resampling.LANCZOS)
                    surface = pygame.image.fromstring(img.tobytes(), img.size, 'RGBA')
                    surface = surface.convert_alpha()
                    self.cache[cache_key] = surface
                    return surface
                except Exception:
                    pass
        
        return None
    
    def _load_local(self, path: Path, size: int, cache_key: str) -> pygame.Surface:
        try:
            img = Image.open(path)
            img = img.convert('RGBA')
            img = img.resize((size, size), Image.Resampling.LANCZOS)
            surface = pygame.image.fromstring(img.tobytes(), img.size, 'RGBA')
            # Apply rounded corners (12px radius, scales with size)
            radius = max(12, size // 25)
            surface = apply_rounded_corners(surface, radius)
            # Convert to display format for faster blitting
            surface = surface.convert_alpha()
            self.cache[cache_key] = surface
            return surface
        except:
            return self.get_placeholder(size)
    
    def _download(self, url: str, size: int, cache_key: str):
        try:
            resp = requests.get(url, timeout=10)
            img = Image.open(BytesIO(resp.content))
            img = img.convert('RGBA')
            img = img.resize((size, size), Image.Resampling.LANCZOS)
            surface = pygame.image.fromstring(img.tobytes(), img.size, 'RGBA')
            # Apply rounded corners
            radius = max(12, size // 25)
            surface = apply_rounded_corners(surface, radius)
            self.cache[cache_key] = surface
        except Exception as e:
            print(f'Error downloading image: {e}')
        finally:
            self.loading.discard(url)

# ============================================
# UI HELPERS
# ============================================

def draw_rounded_rect(surface, color, rect, radius):
    """Draw a rounded rectangle."""
    pygame.draw.rect(surface, color, rect, border_radius=radius)

def apply_rounded_corners(surface, radius):
    """Apply rounded corners to a surface."""
    size = surface.get_size()
    # Create a mask with rounded corners
    mask = pygame.Surface(size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, size[0], size[1]), border_radius=radius)
    # Create result surface
    result = pygame.Surface(size, pygame.SRCALPHA)
    result.blit(surface, (0, 0))
    # Apply mask
    result.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return result

def draw_rounded_triangle(surface, color, points, aa=True):
    """Draw a triangle with anti-aliasing for smoother edges."""
    if aa:
        import pygame.gfxdraw
        pygame.gfxdraw.aapolygon(surface, points, color)
        pygame.gfxdraw.filled_polygon(surface, points, color)
    else:
        pygame.draw.polygon(surface, color, points)

def draw_aa_circle(surface, color, center, radius):
    """Draw an anti-aliased filled circle."""
    import pygame.gfxdraw
    cx, cy = int(center[0]), int(center[1])
    r = int(radius)
    pygame.gfxdraw.aacircle(surface, cx, cy, r, color)
    pygame.gfxdraw.filled_circle(surface, cx, cy, r, color)

def draw_aa_rounded_rect(surface, color, rect, radius):
    """Draw an anti-aliased rounded rectangle using circles for corners."""
    import pygame.gfxdraw
    x, y, w, h = rect
    r = min(radius, w // 2, h // 2)
    
    # If it's basically a circle (width == height and radius >= half), draw as circle
    if w == h and r >= w // 2:
        draw_aa_circle(surface, color, (x + w // 2, y + h // 2), w // 2)
        return
    
    # Draw the main rectangles (center cross)
    pygame.draw.rect(surface, color, (x + r, y, w - 2 * r, h))  # horizontal
    pygame.draw.rect(surface, color, (x, y + r, w, h - 2 * r))  # vertical
    
    # Draw anti-aliased corner circles
    corners = [
        (x + r, y + r),           # top-left
        (x + w - r - 1, y + r),   # top-right
        (x + r, y + h - r - 1),   # bottom-left
        (x + w - r - 1, y + h - r - 1)  # bottom-right
    ]
    for cx, cy in corners:
        pygame.gfxdraw.aacircle(surface, int(cx), int(cy), r, color)
        pygame.gfxdraw.filled_circle(surface, int(cx), int(cy), r, color)

# ============================================
# MAIN APPLICATION
# ============================================

class Berry:
    """Main Berry application."""
    
    def __init__(self, fullscreen: bool = False):
        pygame.init()
        pygame.display.set_caption('🍓 Berry')
        
        # Use hardware acceleration and double buffering for better performance
        flags = pygame.DOUBLEBUF
        if fullscreen:
            flags |= pygame.FULLSCREEN
        # Try hardware surface (may not be available on all systems)
        try:
            self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT), flags | pygame.HWSURFACE)
        except pygame.error:
            self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT), flags)
        self.clock = pygame.time.Clock()
        
        # Hide mouse in fullscreen
        pygame.mouse.set_visible(not fullscreen)
        
        # Fonts
        self.font_large = pygame.font.Font(None, 42)
        self.font_medium = pygame.font.Font(None, 32)
        self.font_small = pygame.font.Font(None, 24)
        
        # Mock mode
        self.mock_mode = MOCK_MODE
        
        # API & data
        self.api = LibrespotAPI(LIBRESPOT_URL)
        self.catalog = Catalog(CATALOG_PATH, mock_mode=self.mock_mode)
        self.catalog.load()
        self.image_cache = ImageCache(IMAGES_DIR)
        
        # Direct catalog operations (no backend needed)
        self.catalog_manager = CatalogManager(CATALOG_PATH, IMAGES_DIR)
        
        # Load icons (56px PNGs with white color, transparent background)
        icons_dir = Path(__file__).parent / 'icons'
        self.icons = {
            'play': pygame.image.load(icons_dir / 'play-fill.png').convert_alpha(),
            'pause': pygame.image.load(icons_dir / 'pause-fill.png').convert_alpha(),
            'prev': pygame.image.load(icons_dir / 'skip-back-fill.png').convert_alpha(),
            'next': pygame.image.load(icons_dir / 'skip-forward-fill.png').convert_alpha(),
            'volume_none': pygame.image.load(icons_dir / 'speaker-none-fill.png').convert_alpha(),
            'volume_low': pygame.image.load(icons_dir / 'speaker-low-fill.png').convert_alpha(),
            'volume_high': pygame.image.load(icons_dir / 'speaker-high-fill.png').convert_alpha(),
            'plus': pygame.image.load(icons_dir / 'plus-circle-fill.png').convert_alpha(),
            'minus': pygame.image.load(icons_dir / 'minus-circle-fill.png').convert_alpha(),
        }
        
        # Volume state (index into VOLUME_LEVELS)
        self.volume_index = 1  # Start at 'low' (75%)
        
        # TempItem and delete mode state
        self.temp_item: Optional[CatalogItem] = None
        self.delete_mode_id: Optional[str] = None
        self.saving = False
        self.deleting = False
        
        # State
        self.now_playing = NowPlaying()
        self.selected_index = 0
        self.connected = self.mock_mode  # Always "connected" in mock mode
        self.needs_refresh = True
        
        # Mock playback state
        self.mock_playing = False
        self.mock_position = 0
        self.mock_duration = 180000  # 3 minutes
        
        # Fase 2: Touch, smooth scroll, auto-play
        self.touch = TouchHandler()
        self.carousel = SmoothCarousel()
        self.carousel.max_index = max(0, len(self.display_items) - 1)
        self.play_timer = PlayTimer()
        self.user_interacting = False  # Block sync while user is swiping
        
        # Fase 3: Sleep mode
        self.sleep_manager = SleepManager()
        
        # Progress tracking
        self.last_progress_save = 0
        self.last_saved_track_uri = None
        
        # Autoplay detection
        self.last_user_play_time = 0
        self.last_user_play_uri = None
        
        # Button hit rects (set during draw)
        self._add_button_rect = None
        self._delete_button_rect = None
        self.last_context_uri = None   # Track context changes for sync
        
        # Progress bar surface cache (avoid allocation per frame)
        self._progress_cache = {}
        
        # Text render cache (avoid re-rendering unchanged text)
        self._text_cache = {}
        self._last_track_key = None
        
        # WebSocket
        self.events = EventListener(LIBRESPOT_WS, self._on_ws_update)
        
        # Running
        self.running = True
    
    def _on_ws_update(self):
        """Called when WebSocket receives an event."""
        self.needs_refresh = True
        print(f'📡 WebSocket event, context: {self.events.context_uri}')
    
    def start(self):
        """Start the application."""
        # Pre-load all catalog images for smooth carousel scrolling
        self.image_cache.preload_catalog(self.catalog.items)
        
        if not self.mock_mode:
            self.events.start()
            
            # Cleanup unused images on startup
            self.catalog_manager.cleanup_unused_images()
            
            # Start status polling thread with error handling
            def poll_status():
                while self.running:
                    try:
                        self._refresh_status()
                    except Exception as e:
                        print(f'❌ Status poll error: {e}')
                    time.sleep(1)
            
            threading.Thread(target=poll_status, daemon=True).start()
            print(f'✅ Started polling {LIBRESPOT_URL}')
        else:
            print('🎭 Running in MOCK MODE - UI testing only')
        
        # Main loop
        while self.running:
            # Adaptive frame rate: 60fps during animation, 30fps when idle
            is_animating = not self.carousel.settled or self.touch.dragging
            target_fps = 60 if is_animating else 30
            dt = self.clock.tick(target_fps) / 1000.0
            
            self._handle_events()
            self._update(dt)
            self._draw()
            pygame.display.flip()
        
        self.events.stop()
        pygame.quit()
    
    def _refresh_status(self):
        """Refresh playback status from librespot."""
        try:
            status = self.api.status()
            self.connected = status is not None or self.api.is_connected()
            
            if status and isinstance(status, dict):
                track = status.get('track') or {}
                if not isinstance(track, dict):
                    track = {}
                    
                playing = not status.get('stopped', True) and not status.get('paused', False)
                
                self.now_playing = NowPlaying(
                    playing=playing,
                    paused=status.get('paused', False),
                    stopped=status.get('stopped', True),
                    context_uri=self.events.context_uri,  # From WebSocket
                    track_name=track.get('name'),
                    track_artist=', '.join(track.get('artist_names', [])) if track.get('artist_names') else None,
                    track_album=track.get('album_name'),
                    track_cover=track.get('album_cover_url'),
                    position=track.get('position', 0),
                    duration=track.get('duration', 0),
                )
                
                # Sync system volume with Spotify volume
                spotify_volume = status.get('volume')
                if spotify_volume is not None:
                    self._sync_volume_from_spotify(spotify_volume)
            else:
                self.now_playing = NowPlaying()
            
            self.needs_refresh = False
            
            # Autoplay detection: clear progress when context finished naturally
            new_context = self.now_playing.context_uri
            old_context = self.last_context_uri
            if (old_context and new_context and 
                old_context != new_context and 
                self.now_playing.playing):
                # Check if this was user-initiated
                recent_user_action = time.time() - self.last_user_play_time < 5
                expected_context = new_context == self.last_user_play_uri
                
                if not recent_user_action and not expected_context:
                    # Autoplay detected - previous context finished naturally
                    print(f'🏁 Context finished: {old_context}')
                    self.catalog_manager.clear_progress(old_context)
            
            # Update tempItem based on nowPlaying
            self._update_temp_item()
            
            # Periodic status log (every 10 seconds when playing)
            # Uses in-place update to avoid cluttering the terminal
            import sys
            if self.now_playing.playing and hasattr(self, '_last_status_log'):
                import time as t
                if t.time() - self._last_status_log > 10:
                    track = (self.now_playing.track_name or "Unknown")[:30]
                    pos = self.now_playing.position // 1000
                    dur = self.now_playing.duration // 1000
                    status_str = f'🎵 {track} ({pos}s/{dur}s)'
                    sys.stdout.write(f'\r{status_str.ljust(60)}')
                    sys.stdout.flush()
                    self._last_status_log = t.time()
            elif self.now_playing.playing:
                import time as t
                self._last_status_log = t.time()
                print(f'\n🎵 Now playing: {self.now_playing.track_name}')
                # Wake from sleep when music starts
                if self.sleep_manager.is_sleeping:
                    self.sleep_manager.wake_up()
        except Exception as e:
            print(f'❌ Status refresh error: {e}')
            import traceback
            traceback.print_exc()
    
    def _handle_events(self):
        """Handle pygame events."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            
            # Handle sleep mode - wake up on any touch
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if self.sleep_manager.is_sleeping:
                    self.sleep_manager.wake_up()
                    continue  # Don't process this touch further
                self.sleep_manager.reset_timer()
                self._handle_touch_down(event.pos)
            
            elif event.type == pygame.KEYDOWN:
                # Wake up on key press if sleeping
                if self.sleep_manager.is_sleeping:
                    self.sleep_manager.wake_up()
                    continue
                self.sleep_manager.reset_timer()
                
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_LEFT:
                    self._navigate(-1)
                elif event.key == pygame.K_RIGHT:
                    self._navigate(1)
                elif event.key == pygame.K_SPACE or event.key == pygame.K_RETURN:
                    self._toggle_play()
                elif event.key == pygame.K_n:
                    self.api.next()
                elif event.key == pygame.K_p:
                    self.api.prev()
            
            elif event.type == pygame.MOUSEMOTION:
                if self.touch.dragging:
                    self.sleep_manager.reset_timer()
                    self.touch.on_move(event.pos)
            
            elif event.type == pygame.MOUSEBUTTONUP:
                if not self.sleep_manager.is_sleeping:
                    self._handle_touch_up(event.pos)
    
    def _check_add_delete_button_click(self, pos) -> bool:
        """Check if click is on add/delete button. Returns True if handled."""
        x, y = pos
        
        # Check add button (for temp items)
        if hasattr(self, '_add_button_rect') and self._add_button_rect:
            bx, by, bw, bh = self._add_button_rect
            if bx <= x <= bx + bw and by <= y <= by + bh:
                self._save_temp_item()
                return True
        
        # Check delete button (for delete mode)
        if hasattr(self, '_delete_button_rect') and self._delete_button_rect:
            bx, by, bw, bh = self._delete_button_rect
            if bx <= x <= bx + bw and by <= y <= by + bh:
                self._delete_current_item()
                return True
        
        return False
    
    def _trigger_delete_mode(self):
        """Trigger delete mode for the currently selected item."""
        items = self.display_items
        if not items or self.selected_index >= len(items):
            return
        
        item = items[self.selected_index]
        
        # Only for non-temp items
        if item.is_temp:
            return
        
        print(f'🗑️ Delete mode: {item.name}')
        self.delete_mode_id = item.id
    
    def _save_temp_item(self):
        """Save the current temp item to catalog."""
        if not self.temp_item or self.saving:
            return
        
        self.saving = True
        print(f'💾 Saving: {self.temp_item.name}')
        
        # Build item data
        item_data = {
            'type': self.temp_item.type,
            'uri': self.temp_item.uri,
            'name': self.temp_item.name,
            'artist': self.temp_item.artist,
            'image': self.temp_item.image,
        }
        
        # Save directly via CatalogManager (no backend needed)
        success = self.catalog_manager.save_item(item_data)
        
        if success:
            # Reload catalog
            self.catalog.load()
            self._update_carousel_max_index()
            # Pre-load new images
            self.image_cache.preload_catalog(self.catalog.items)
            # Clear temp item (it's now in catalog)
            self.temp_item = None
        
        self.saving = False
    
    def _delete_current_item(self):
        """Delete the current item from catalog."""
        if not self.delete_mode_id or self.deleting:
            return
        
        self.deleting = True
        item_id = self.delete_mode_id
        old_index = self.selected_index
        
        # Find item name for logging
        item = next((i for i in self.catalog.items if i.id == item_id), None)
        if item:
            print(f'🗑️ Deleting: {item.name}')
        
        # Delete directly via CatalogManager (no backend needed)
        success = self.catalog_manager.delete_item(item_id)
        
        if success:
            # Reload catalog
            self.catalog.load()
            self._update_carousel_max_index()
            
            # Navigate to previous item (or first if at start)
            new_index = max(0, old_index - 1)
            if len(self.display_items) > 0:
                new_index = min(new_index, len(self.display_items) - 1)
                self.selected_index = new_index
                self.carousel.scroll_x = float(new_index)
                self.carousel.set_target(new_index)
                
                # Play the new item
                new_item = self.display_items[new_index]
                if not new_item.is_temp:
                    print(f'▶️ Playing after delete: {new_item.name}')
                    self._play_item(new_item.uri)
        
        self.delete_mode_id = None
        self.deleting = False
    
    def _handle_touch_down(self, pos):
        """Handle touch/mouse down."""
        x, y = pos
        
        # Check for add/delete button clicks first
        if self._check_add_delete_button_click(pos):
            return
        
        # Cancel delete mode if clicking elsewhere
        if self.delete_mode_id:
            self.delete_mode_id = None
        
        # Only handle carousel swipes in carousel area
        if CAROUSEL_Y <= y <= CAROUSEL_Y + COVER_SIZE + 50:
            self.touch.on_down(pos)
            self.user_interacting = True
            self.play_timer.cancel()
        else:
            # Direct button handling for control area
            self._handle_button_tap(pos)
    
    def _handle_touch_up(self, pos):
        """Handle touch/mouse up."""
        if not self.touch.dragging:
            return
        
        # Calculate visual position BEFORE resetting drag (prevents snap-back)
        drag_index_offset = -self.touch.drag_offset / (COVER_SIZE + COVER_SPACING)
        visual_position = self.selected_index + drag_index_offset
        
        action, velocity = self.touch.on_up(pos)
        # Note: user_interacting stays True until _update determines it's safe
        
        # Sync scroll_x to where user dragged to
        self.carousel.scroll_x = visual_position
        
        x, y = pos
        center_x = SCREEN_WIDTH // 2
        
        if action == 'left' or action == 'right':
            # Position + Velocity bonus approach:
            # - Base: nearest item from visual_position
            # - Bonus: velocity adds 0-3 extra items
            # - Max total: 5 items from starting position
            
            abs_vel = abs(velocity)
            
            # Calculate velocity bonus (0-3 extra items based on speed)
            if abs_vel < 1.0:
                velocity_bonus = 0
            elif abs_vel < 2.0:
                velocity_bonus = 1
            elif abs_vel < 3.5:
                velocity_bonus = 2
            else:
                velocity_bonus = 3
            
            # Base target: round to nearest item from visual position
            base_target = round(visual_position)
            
            # Apply bonus in swipe direction
            if velocity < 0:  # swipe left = go to higher index
                target = base_target + velocity_bonus
            else:  # swipe right = go to lower index
                target = base_target - velocity_bonus
            
            # Ensure we move at least 1 item if there was real movement
            moved_items = abs(visual_position - self.selected_index)
            if moved_items > 0.2 and target == self.selected_index:
                # User dragged but would end up at same item - move 1 in swipe direction
                target = self.selected_index + (-1 if velocity > 0 else 1)
            
            # Clamp to valid range and max 5 items from start
            max_jump = 5
            target = max(self.selected_index - max_jump, min(target, self.selected_index + max_jump))
            target = max(0, min(target, len(self.display_items) - 1))
            
            self._snap_to(target)
        elif action == 'tap':
            # Tap in carousel area
            if x < center_x - 100:
                self._navigate(-1)
            elif x > center_x + 100:
                self._navigate(1)
        else:
                self._toggle_play()
    
    def _handle_button_tap(self, pos):
        """Handle direct tap on control buttons."""
        x, y = pos
        center_x = SCREEN_WIDTH // 2
        btn_spacing = 145
        
        # Volume button position (aligned with right cover edge)
        right_cover_edge = center_x + (COVER_SIZE + COVER_SPACING) + COVER_SIZE_SMALL // 2
        vol_x = right_cover_edge - BTN_SIZE // 2
        
        if CONTROLS_Y - PLAY_BTN_SIZE <= y <= CONTROLS_Y + PLAY_BTN_SIZE:
            # Prev button
            if center_x - btn_spacing - BTN_SIZE <= x <= center_x - btn_spacing + BTN_SIZE:
                print('  → Prev track')
                self.api.prev()
            # Play button
            elif center_x - PLAY_BTN_SIZE <= x <= center_x + PLAY_BTN_SIZE:
                print('  → Toggle play')
                self._toggle_play()
        # Next button
            elif center_x + btn_spacing - BTN_SIZE <= x <= center_x + btn_spacing + BTN_SIZE:
                print('  → Next track')
                self.api.next()
            # Volume button
            elif vol_x - BTN_SIZE <= x <= vol_x + BTN_SIZE:
                self._toggle_volume()
    
    def _snap_to(self, target_index: int):
        """Snap carousel to a specific index."""
        items = self.display_items
        if not items:
            return
        
        target_index = max(0, min(target_index, len(items) - 1))
        
        if target_index != self.selected_index:
            self.selected_index = target_index
            self.carousel.set_target(target_index)
            
            # Start play timer for new selection (only for non-temp items)
            item = items[target_index]
            if not item.is_temp and not self._is_item_playing(item):
                self.play_timer.start(item)
            else:
                self.play_timer.cancel()
        else:
            # Same index but might need to animate back to center
            self.carousel.set_target(target_index)
    
    def _navigate(self, direction: int):
        """Navigate carousel by direction (-1 = prev, 1 = next)."""
        items = self.display_items
        if not items:
            return
        
        new_index = self.selected_index + direction
        new_index = max(0, min(new_index, len(items) - 1))
        
        self._snap_to(new_index)
    
    def _is_item_playing(self, item) -> bool:
        """Check if an item is currently playing."""
        if not self.now_playing.context_uri:
            return False
        return item.uri == self.now_playing.context_uri
    
    
    def _toggle_play(self):
        items = self.display_items
        if self.mock_mode:
            self.mock_playing = not self.mock_playing
            if self.mock_playing and items:
                item = items[self.selected_index]
                ct = item.current_track if isinstance(item.current_track, dict) else None
                self.now_playing = NowPlaying(
                    playing=True,
                    context_uri=item.uri,
                    track_name=ct.get('name', item.name) if ct else item.name,
                    track_artist=ct.get('artist', item.artist) if ct else item.artist,
                    position=self.mock_position,
                    duration=self.mock_duration,
                )
            else:
                self.now_playing = NowPlaying(paused=True, context_uri=self.now_playing.context_uri)
            return
        
        if self.now_playing.playing:
            print('⏸ Pausing...')
            self.api.pause()
        elif self.now_playing.paused:
            print('▶️ Resuming...')
            self.api.resume()
        elif items:
            item = items[self.selected_index]
            print(f'▶️ Playing {item.name} ({item.uri})')
            self._play_item(item.uri)
    
    def _play_item(self, uri: str, from_beginning: bool = False):
        """Play an item with optional resume from saved progress."""
        print(f'▶️ _play_item called with: {uri}')
        
        # Track user action for autoplay detection
        self.last_user_play_time = time.time()
        self.last_user_play_uri = uri
        
        # Save current progress before switching
        if self.now_playing.context_uri and self.now_playing.context_uri != uri:
            self._save_playback_progress()
        
        # Check for saved progress (unless from_beginning is True)
        saved_progress = None
        skip_to_uri = None
        if not from_beginning:
            saved_progress = self.catalog_manager.get_progress(uri)
            if saved_progress:
                skip_to_uri = saved_progress.get('uri')  # Track URI to resume at
        
        # Play the item (with skip_to_uri if resuming)
        success = self.api.play(uri, skip_to_uri=skip_to_uri)
        
        # If we have saved progress with position, seek to it after a brief delay
        if success and saved_progress and saved_progress.get('position', 0) > 0:
            def seek_later():
                time.sleep(0.5)  # Wait for playback to start
                try:
                    position = saved_progress['position']
                    response = self.api.session.post(
                        f'{LIBRESPOT_URL}/player/seek',
                        json={'position': position},
                        timeout=2
                    )
                    if response.ok:
                        print(f'📍 Seeked to {position // 1000}s')
                except Exception as e:
                    print(f'⚠️ Seek error: {e}')
            
            threading.Thread(target=seek_later, daemon=True).start()
        
        return success
    
    def _toggle_volume(self):
        """Toggle between volume levels."""
        import subprocess
        
        # Cycle to next level
        self.volume_index = (self.volume_index + 1) % len(VOLUME_LEVELS)
        level_info = VOLUME_LEVELS[self.volume_index]
        level = level_info['level']
        
        print(f'🔊 Volume: {level}%')
        
        # Set Spotify Connect volume
        self.api.set_volume(level)
        
        # Set Pi system volume (ALSA) to match
        self._set_system_volume(level)
    
    def _set_system_volume(self, level: int):
        """Set the Pi's ALSA system volume (Linux only)."""
        import subprocess
        import sys
        
        # Only attempt on Linux (Raspberry Pi)
        if sys.platform != 'linux':
            return
        
        try:
            subprocess.run(['amixer', 'set', 'Master', f'{level}%'],
                          capture_output=True, check=True)
        except Exception as e:
            print(f'  ⚠️ Could not set system volume: {e}')
    
    def _sync_volume_from_spotify(self, spotify_volume: int):
        """Sync system volume when Spotify volume changes externally."""
        # Track last synced volume to avoid redundant updates
        if not hasattr(self, '_last_synced_volume'):
            self._last_synced_volume = -1
        
        # Only sync if volume actually changed
        if spotify_volume == self._last_synced_volume:
            return
        
        self._last_synced_volume = spotify_volume
        
        # Update system volume to match
        self._set_system_volume(spotify_volume)
        
        # Update icon to closest matching level
        closest_index = 0
        closest_diff = abs(VOLUME_LEVELS[0]['level'] - spotify_volume)
        for i, level_info in enumerate(VOLUME_LEVELS):
            diff = abs(level_info['level'] - spotify_volume)
            if diff < closest_diff:
                closest_diff = diff
                closest_index = i
        
        if closest_index != self.volume_index:
            self.volume_index = closest_index
            print(f'🔊 Volume synced from Spotify: {spotify_volume}%')
    
    def _update_temp_item(self):
        """Update tempItem based on current playback context."""
        context_uri = self.now_playing.context_uri
        
        # No context = no tempItem
        if not context_uri:
            if self.temp_item:
                self.temp_item = None
                self._update_carousel_max_index()
            return
        
        # Check if already in catalog
        in_catalog = any(item.uri == context_uri for item in self.catalog.items)
        if in_catalog:
            if self.temp_item:
                self.temp_item = None
                self._update_carousel_max_index()
            return
        
        # Create or update tempItem
        is_playlist = 'playlist' in context_uri
        
        # For playlists, get collected covers for composite image
        collected_covers = None
        if is_playlist and context_uri in self.catalog_manager.playlist_covers:
            collected_covers = list(self.catalog_manager.playlist_covers[context_uri].values())
        
        # Check if we need to update (new context or new covers collected)
        current_cover_count = len(self.temp_item.images or []) if self.temp_item else 0
        new_cover_count = len(collected_covers or [])
        
        # Debug: show cover counts
        if is_playlist and new_cover_count != current_cover_count:
            print(f'📸 TempItem cover check: {current_cover_count} -> {new_cover_count}')
        
        needs_update = (
            not self.temp_item or 
            self.temp_item.uri != context_uri or
            new_cover_count > current_cover_count  # New covers collected
        )
        
        if needs_update:
            new_temp = CatalogItem(
                id='temp',
                uri=context_uri,
                name=self.now_playing.track_album or ('Playlist' if is_playlist else 'Album'),
                type='playlist' if is_playlist else 'album',
                artist=self.now_playing.track_artist,
                image=self.now_playing.track_cover,
                images=collected_covers,  # Composite covers for playlists
                is_temp=True
            )
            
            is_new = not self.temp_item or self.temp_item.uri != context_uri
            self.temp_item = new_temp
            self._update_carousel_max_index()
            
            if is_new:
                print(f'📎 TempItem: {new_temp.name} ({new_temp.type})')
            elif new_cover_count > current_cover_count:
                print(f'📸 TempItem covers: {new_cover_count}/4')
    
    def _update_carousel_max_index(self):
        """Update carousel max index when items change."""
        self.carousel.max_index = max(0, len(self.display_items) - 1)
    
    @property
    def display_items(self) -> List[CatalogItem]:
        """Return catalog items + tempItem if present."""
        if self.temp_item:
            return self.catalog.items + [self.temp_item]
        return self.catalog.items
    
    def _update(self, dt: float):
        """Update application state."""
        # Clamp selected index
        items = self.display_items
        if items:
            self.selected_index = max(0, min(self.selected_index, len(items) - 1))
        
        # Update smooth scroll carousel
        was_animating = not self.carousel.settled
        self.carousel.update(dt)
        
        # When carousel settles, update selected_index and start play timer
        if was_animating and self.carousel.settled:
            new_index = self.carousel.target_index
            if new_index != self.selected_index and items:
                self.selected_index = new_index
                # Start play timer for new selection (only for non-temp items)
                if new_index < len(items):
                    item = items[new_index]
                    if not item.is_temp and not self._is_item_playing(item):
                        self.play_timer.start(item)
        
        # Check for long press to trigger delete mode
        if self.touch.check_long_press():
            self._trigger_delete_mode()
        
        # Update user_interacting state - stays True while anything is in progress
        self.user_interacting = (
            self.touch.dragging or              # still dragging
            not self.carousel.settled or        # carousel animating
            self.play_timer.item is not None    # timer counting down
        )
        
        # Check play timer
        item_to_play = self.play_timer.check()
        if item_to_play:
            print(f'▶️ Auto-playing: {item_to_play.name} ({item_to_play.uri})')
            self._play_item(item_to_play.uri)
        
        # Sync carousel to playing (when not user interacting)
        self._sync_to_playing()
        
        # Mock mode: simulate playback progress
        if self.mock_mode and self.mock_playing:
            self.mock_position += int(dt * 1000)
            if self.mock_position >= self.mock_duration:
                self.mock_position = 0  # Loop
            self.now_playing.position = self.mock_position
        
        # Progress tracking: save every 10 seconds during playback
        if (self.now_playing.playing and 
            not self.mock_mode and
            time.time() - self.last_progress_save > 10):
            self._save_playback_progress()
        
        # Collect playlist covers during playback
        if (self.now_playing.playing and 
            'playlist' in (self.now_playing.context_uri or '')):
            self.catalog_manager.collect_cover_for_playlist(
                self.now_playing.context_uri,
                self.now_playing.track_cover
            )
        
        # Check sleep mode - pass whether music is playing
        self.sleep_manager.check_sleep(self.now_playing.playing)
    
    def _save_playback_progress(self):
        """Save current playback position to catalog (for resume)."""
        try:
            # Get current status from REST API (more accurate than WebSocket which can lag)
            status = self.api.status()
            if not status or not status.get('track'):
                return
            
            # IMPORTANT: Use context_uri from REST API, not from WebSocket!
            # WebSocket can lag behind causing tracks to be saved to wrong album
            context_uri = status.get('context_uri')
            if not context_uri:
                # Fallback to WebSocket if REST doesn't have it
                context_uri = self.now_playing.context_uri
            
            if not context_uri:
                return
            
            track = status['track']
            track_uri = track.get('uri')
            position = track.get('position', 0)
            
            # Save progress
            self.catalog_manager.save_progress(
                context_uri,
                track_uri,
                position,
                track.get('name'),
                ', '.join(track.get('artist_names', []))
            )
            
            self.last_saved_track_uri = track_uri
            self.last_progress_save = time.time()
            
        except Exception as e:
            print(f'⚠️ Error saving progress: {e}')
    
    def _sync_to_playing(self):
        """Sync carousel to currently playing item (external changes)."""
        items = self.display_items
        if not items:
            return
        
        # Don't sync while user is interacting
        if self.user_interacting:
            return
        
        # Don't sync while play timer is active
        if self.play_timer.item:
            return
        
        # Don't sync if carousel is still animating
        if not self.carousel.settled:
            return
        
        # Don't sync during cooldown after firing play timer
        # (prevents syncing to old context before new one arrives)
        if time.time() - self.play_timer.last_fired_time < self.play_timer.SYNC_COOLDOWN:
            return
        
        context_uri = self.now_playing.context_uri
        
        # Skip if no context or context hasn't changed
        if not context_uri:
            return
        if context_uri == self.last_context_uri:
            return
        
        # Skip sync for items we just played via timer
        if context_uri == self.play_timer.last_played_uri:
            self.play_timer.last_played_uri = None
            self.last_context_uri = context_uri
            return
        
        # Find playing item in display items (includes tempItem)
        playing_index = None
        for i, item in enumerate(items):
            if item.uri == context_uri:
                playing_index = i
                break
        
        if playing_index is None:
            return
        
        # Only sync if different from current selection
        if playing_index != self.selected_index:
            print(f'🔄 Syncing to playing: {items[playing_index].name}')
            self.selected_index = playing_index
            self.carousel.set_target(playing_index)
        
        self.last_context_uri = context_uri
    
    def _draw(self):
        """Draw the application."""
        # Sleep mode - show black screen only
        if self.sleep_manager.is_sleeping:
            self.screen.fill((0, 0, 0))
            return
        
        # Clear button hit rects (will be set if buttons are drawn)
        self._add_button_rect = None
        self._delete_button_rect = None
        
        # Background with gradient
        self._draw_background()
        
        # Connection status
        if not self.connected:
            self._draw_disconnected()
            return
        
        if not self.display_items:
            self._draw_empty_state()
            return
        
        # Carousel
        self._draw_carousel()
        
        # Track info
        self._draw_track_info()
        
        # Controls
        self._draw_controls()
    
    def _draw_background(self):
        """Draw pre-rendered background."""
        # Use cached background (created once in __init__)
        if not hasattr(self, '_bg_cache'):
            self._bg_cache = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            self._bg_cache.fill(COLORS['bg_primary'])
            # Pre-render gradient with purple accent tint
            for y in range(150):
                alpha = int(30 * (1 - y / 150))
                color = (
                    min(255, COLORS['bg_primary'][0] + int(alpha * 0.75)),  # Purple: R
                    min(255, COLORS['bg_primary'][1] + int(alpha * 0.4)),   # Purple: G
                    min(255, COLORS['bg_primary'][2] + alpha),              # Purple: B (strongest)
                )
                pygame.draw.line(self._bg_cache, color, (0, y), (SCREEN_WIDTH, y))
            self._bg_cache = self._bg_cache.convert()
        
        self.screen.blit(self._bg_cache, (0, 0))
    
    def _draw_disconnected(self):
        """Draw disconnected state."""
        text = self.font_large.render('Connecting to Berry...', True, COLORS['text_secondary'])
        rect = text.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2))
        self.screen.blit(text, rect)
    
    def _draw_empty_state(self):
        """Draw empty catalog state."""
        # Icon
        icon = self.font_large.render('🎧', True, COLORS['text_primary'])
        icon_rect = icon.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 40))
        self.screen.blit(icon, icon_rect)
        
        # Title
        title = self.font_large.render('No music yet', True, COLORS['text_primary'])
        title_rect = title.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 20))
        self.screen.blit(title, title_rect)
        
        # Subtitle
        sub = self.font_medium.render('Play music via Spotify and tap + to add', True, COLORS['text_secondary'])
        sub_rect = sub.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 60))
        self.screen.blit(sub, sub_rect)
    
    def _draw_carousel(self):
        """Draw album cover carousel with smooth scrolling and progress bar overlay."""
        center_x = SCREEN_WIDTH // 2
        y = CAROUSEL_Y
        
        # Use smooth scroll position, with drag offset during swipe
        scroll_offset = self.carousel.scroll_x
        if self.touch.dragging:
            # Convert pixel drag to index offset
            drag_index_offset = -self.touch.drag_offset / (COVER_SIZE + COVER_SPACING)
            scroll_offset = self.selected_index + drag_index_offset
        
        # Clamp scroll_offset to valid range to prevent empty draws
        items = self.display_items
        max_index = max(0, len(items) - 1)
        scroll_offset = max(0, min(scroll_offset, max_index))
        
        # Only draw visible items (max 5)
        start_i = max(0, int(scroll_offset) - 2)
        end_i = min(len(items), int(scroll_offset) + 3)
        
        # Track center cover position for progress bar and buttons
        center_cover_rect = None
        center_item = None
        
        for i in range(start_i, end_i):
            item = items[i]
            
            # Calculate position based on smooth scroll
            offset = i - scroll_offset
            x = center_x + offset * (COVER_SIZE + COVER_SPACING)
            
            # Use only 2 sizes for better caching (snap at 0.5 threshold)
            is_center = abs(offset) < 0.5
            size = COVER_SIZE if is_center else COVER_SIZE_SMALL
            
            # Center the cover
            draw_x = int(x - size // 2)
            draw_y = y + (COVER_SIZE - size) // 2
            
            # Skip if off screen
            if draw_x + size < 0 or draw_x > SCREEN_WIDTH:
                continue
            
            # Get cover image (use dimmed cache key for non-center)
            # For playlists with multiple images, use composite cover
            is_playlist = item.type == 'playlist' or 'playlist' in (item.uri or '')
            has_multiple_images = item.images and len(item.images) > 1
            
            if is_center:
                if is_playlist and has_multiple_images:
                    cover = self.image_cache.get_composite(item.images, size)
                else:
                    cover = self.image_cache.get(item.image, size)
                center_cover_rect = (draw_x, draw_y, size, size)
                center_item = item
            else:
                if is_playlist and has_multiple_images:
                    # Use cached dimmed composite (no per-frame copy needed)
                    cover = self.image_cache.get_composite_dimmed(item.images, size)
                else:
                    cover = self.image_cache.get_dimmed(item.image, size)
            
            self.screen.blit(cover, (draw_x, draw_y))
            
        # Draw progress bar inside center cover
        if center_cover_rect and center_item:
            self._draw_cover_progress(center_cover_rect, center_item)
            
            # Draw + button for temp items, or - button for delete mode
            if center_item.is_temp:
                self._draw_add_button(center_cover_rect)
            elif self.delete_mode_id == center_item.id:
                self._draw_delete_button(center_cover_rect)
    
    def _draw_track_info(self):
        """Draw track name and artist (with caching to avoid re-rendering)."""
        items = self.display_items
        item = items[self.selected_index] if items and self.selected_index < len(items) else None
        if not item:
            return
        
        # Determine what to show
        if self.now_playing.context_uri == item.uri and self.now_playing.track_name:
            name = self.now_playing.track_name
            artist = self.now_playing.track_artist or ''
        elif item.current_track and isinstance(item.current_track, dict):
            name = item.current_track.get('name', item.name) or item.name
            artist = item.current_track.get('artist', item.artist or '') or item.artist or ''
        else:
            name = item.name or 'Unknown'
            artist = item.artist or ''
        
        # Check if text changed (cache key)
        track_key = (name, artist)
        if track_key != self._last_track_key:
            self._last_track_key = track_key
            
            # Truncate if too long
            max_width = SCREEN_WIDTH - 100
            display_name = name
            
            # Track name
            name_surface = self.font_large.render(display_name, True, COLORS['text_primary'])
            if name_surface.get_width() > max_width:
                while name_surface.get_width() > max_width - 30 and len(display_name) > 3:
                    display_name = display_name[:-1]
                name_surface = self.font_large.render(display_name + '...', True, COLORS['text_primary'])
            
            self._text_cache['name_surface'] = name_surface
            self._text_cache['name_rect'] = name_surface.get_rect(center=(SCREEN_WIDTH // 2, TRACK_INFO_Y))
            
            # Artist
            if artist:
                artist_surface = self.font_medium.render(artist, True, COLORS['text_secondary'])
                self._text_cache['artist_surface'] = artist_surface
                self._text_cache['artist_rect'] = artist_surface.get_rect(center=(SCREEN_WIDTH // 2, TRACK_INFO_Y + 35))
            else:
                self._text_cache['artist_surface'] = None
        
        # Blit cached surfaces
        self.screen.blit(self._text_cache['name_surface'], self._text_cache['name_rect'])
        if self._text_cache.get('artist_surface'):
            self.screen.blit(self._text_cache['artist_surface'], self._text_cache['artist_rect'])
    
    def _draw_cover_progress(self, cover_rect, item):
        """Draw progress bar at the bottom edge of the cover (accent only, clipped by corners)."""
        cover_x, cover_y, cover_w, cover_h = cover_rect
        
        # Only show progress for playing item
        if self.now_playing.context_uri != item.uri:
            return
        
        position = self.now_playing.position
        duration = self.now_playing.duration
        progress = position / duration if duration > 0 else 0
        
        if progress <= 0:
            return
        
        bar_height = PROGRESS_BAR_HEIGHT
        fill_width = int(cover_w * min(progress, 1.0))
        
        if fill_width <= 0:
            return
        
        # Cache progress bar mask per size (avoid recreating each frame)
        mask_key = f'_progress_mask_{cover_w}'
        if mask_key not in self._progress_cache:
            radius = max(12, cover_w // 25)
            mask = pygame.Surface((cover_w, cover_h), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, cover_w, cover_h), border_radius=radius)
            self._progress_cache[mask_key] = mask
        
        # Reuse cached progress surface (avoid allocation per frame)
        surf_key = f'_progress_surf_{cover_w}'
        if surf_key not in self._progress_cache:
            self._progress_cache[surf_key] = pygame.Surface((cover_w, cover_h), pygame.SRCALPHA)
        
        progress_surf = self._progress_cache[surf_key]
        progress_surf.fill((0, 0, 0, 0))  # Clear (faster than recreating)
        
        # Draw progress bar at the bottom of this surface
        pygame.draw.rect(progress_surf, COLORS['accent'],
                        (0, cover_h - bar_height, fill_width, bar_height))
        
        # Apply cached mask to clip progress bar to rounded corners
        progress_surf.blit(self._progress_cache[mask_key], (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        
        # Blit to screen
        self.screen.blit(progress_surf, (cover_x, cover_y))
    
    def _draw_controls(self):
        """Draw playback control buttons with anti-aliased shapes."""
        center_x = SCREEN_WIDTH // 2
        y = CONTROLS_Y
        btn_spacing = 145  # Space between prev/play/next buttons
        
        # Prev button (AA circle)
        prev_x = center_x - btn_spacing
        prev_center = (prev_x, y)
        draw_aa_circle(self.screen, COLORS['bg_elevated'], prev_center, BTN_SIZE // 2)
        self._draw_prev_icon(prev_center)
        
        # Play/Pause button (larger AA circle, accent color)
        play_center = (center_x, y)
        draw_aa_circle(self.screen, COLORS['accent'], play_center, PLAY_BTN_SIZE // 2)
        if self.now_playing.playing:
            self._draw_pause_icon(play_center)
        else:
            self._draw_play_icon(play_center)
        
        # Next button (AA circle)
        next_x = center_x + btn_spacing
        next_center = (next_x, y)
        draw_aa_circle(self.screen, COLORS['bg_elevated'], next_center, BTN_SIZE // 2)
        self._draw_next_icon(next_center)
        
        # Volume button - aligned with right edge of right cover
        # Right cover edge = center_x + (COVER_SIZE + COVER_SPACING) + COVER_SIZE_SMALL/2
        right_cover_edge = center_x + (COVER_SIZE + COVER_SPACING) + COVER_SIZE_SMALL // 2
        vol_x = right_cover_edge - BTN_SIZE // 2
        vol_center = (vol_x, y)
        draw_aa_circle(self.screen, COLORS['bg_elevated'], vol_center, BTN_SIZE // 2)
        self._draw_volume_icon(vol_center)
    
    def _draw_play_icon(self, center):
        """Draw play icon from PNG."""
        icon = self.icons['play']
        rect = icon.get_rect(center=center)
        self.screen.blit(icon, rect)
    
    def _draw_pause_icon(self, center):
        """Draw pause icon from PNG."""
        icon = self.icons['pause']
        rect = icon.get_rect(center=center)
        self.screen.blit(icon, rect)
    
    def _draw_prev_icon(self, center):
        """Draw previous icon from PNG."""
        icon = self.icons['prev']
        rect = icon.get_rect(center=center)
        self.screen.blit(icon, rect)
    
    def _draw_next_icon(self, center):
        """Draw next icon from PNG."""
        icon = self.icons['next']
        rect = icon.get_rect(center=center)
        self.screen.blit(icon, rect)
    
    def _draw_volume_icon(self, center):
        """Draw volume icon from PNG based on current volume level."""
        icon_key = VOLUME_LEVELS[self.volume_index]['icon']
        icon = self.icons[icon_key]
        rect = icon.get_rect(center=center)
        self.screen.blit(icon, rect)
    
    def _draw_add_button(self, cover_rect):
        """Draw + button on cover for temp items."""
        cover_x, cover_y, cover_w, cover_h = cover_rect
        
        # Button in bottom-right corner of cover (larger for easy tapping)
        btn_size = 100  # Bigger tap target
        icon_size = 72  # Larger icon
        margin = 16
        btn_x = cover_x + cover_w - btn_size - margin
        btn_y = cover_y + cover_h - btn_size - margin
        center = (btn_x + btn_size // 2, btn_y + btn_size // 2)
        
        # Scale and tint icon to accent color (no background circle)
        icon = self.icons['plus']
        scaled_icon = pygame.transform.smoothscale(icon, (icon_size, icon_size))
        # Tint icon to accent color
        tinted = scaled_icon.copy()
        tinted.fill(COLORS['accent'], special_flags=pygame.BLEND_RGB_MULT)
        
        icon_rect = tinted.get_rect(center=center)
        self.screen.blit(tinted, icon_rect)
        
        # Store button rect for hit testing
        self._add_button_rect = (btn_x, btn_y, btn_size, btn_size)
    
    def _draw_delete_button(self, cover_rect):
        """Draw - button on cover for delete mode."""
        cover_x, cover_y, cover_w, cover_h = cover_rect
        
        # Button in bottom-right corner of cover (larger for easy tapping)
        btn_size = 100  # Bigger tap target
        icon_size = 72  # Larger icon
        margin = 16
        btn_x = cover_x + cover_w - btn_size - margin
        btn_y = cover_y + cover_h - btn_size - margin
        center = (btn_x + btn_size // 2, btn_y + btn_size // 2)
        
        # Scale and tint icon to error color (no background circle)
        icon = self.icons['minus']
        scaled_icon = pygame.transform.smoothscale(icon, (icon_size, icon_size))
        # Tint icon to error color
        tinted = scaled_icon.copy()
        tinted.fill(COLORS['error'], special_flags=pygame.BLEND_RGB_MULT)
        
        icon_rect = tinted.get_rect(center=center)
        self.screen.blit(tinted, icon_rect)
        
        # Store button rect for hit testing
        self._delete_button_rect = (btn_x, btn_y, btn_size, btn_size)

# ============================================
# ENTRY POINT
# ============================================

def main():
    fullscreen = '--fullscreen' in sys.argv or '-f' in sys.argv
    mock_mode = '--mock' in sys.argv or '-m' in sys.argv
    
    print('🍓 Berry Native')
    if mock_mode:
        print('   Mode: MOCK (UI testing)')
    else:
        print(f'   Librespot: {LIBRESPOT_URL}')
    print(f'   Screen: {SCREEN_WIDTH}x{SCREEN_HEIGHT}')
    print(f'   Fullscreen: {fullscreen}')
    print()
    print('Controls:')
    print('   ← →     Navigate carousel')
    print('   Space   Play/Pause')
    print('   N       Next track')
    print('   P       Previous track')
    print('   Esc     Quit')
    print()
    
    app = Berry(fullscreen=fullscreen)
    app.start()

if __name__ == '__main__':
    main()
