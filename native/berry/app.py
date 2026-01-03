"""
Berry Application - Main application class.
"""
import os
import sys
import time
import logging
import threading
import subprocess
from typing import Optional, List

import pygame

from .config import (
    SCREEN_WIDTH, SCREEN_HEIGHT, COLORS,
    LIBRESPOT_URL, LIBRESPOT_WS, 
    CATALOG_PATH, IMAGES_DIR, ICONS_DIR,
    MOCK_MODE, VOLUME_LEVELS,
    COVER_SIZE, COVER_SIZE_SMALL, COVER_SPACING,
    CAROUSEL_Y, CONTROLS_Y, BTN_SIZE, PLAY_BTN_SIZE,
    PROGRESS_SAVE_INTERVAL,
)
from .models import CatalogItem, NowPlaying
from .api import LibrespotAPI, CatalogManager
from .handlers import TouchHandler, EventListener
from .managers import SleepManager, SmoothCarousel, PlayTimer, PerformanceMonitor
from .ui import ImageCache, Renderer

logger = logging.getLogger(__name__)


class Berry:
    """Main Berry application."""
    
    def __init__(self, fullscreen: bool = False):
        pygame.init()
        pygame.display.set_caption('Berry')
        
        # Display setup
        flags = pygame.DOUBLEBUF
        if fullscreen:
            flags |= pygame.FULLSCREEN
        
        try:
            self.screen = pygame.display.set_mode(
                (SCREEN_WIDTH, SCREEN_HEIGHT), 
                flags | pygame.HWSURFACE
            )
        except pygame.error:
            self.screen = pygame.display.set_mode(
                (SCREEN_WIDTH, SCREEN_HEIGHT), 
                flags
            )
        
        self.clock = pygame.time.Clock()
        pygame.mouse.set_visible(not fullscreen)
        
        self._log_video_info()
        
        # Mock mode
        self.mock_mode = MOCK_MODE
        
        # API & Catalog
        self.api = LibrespotAPI(LIBRESPOT_URL)
        self.catalog_manager = CatalogManager(CATALOG_PATH, IMAGES_DIR, mock_mode=self.mock_mode)
        self.catalog_manager.load()
        
        # UI Components
        self.image_cache = ImageCache(IMAGES_DIR)
        self.icons = self._load_icons()
        self.renderer = Renderer(self.screen, self.image_cache, self.icons)
        
        # Handlers
        self.touch = TouchHandler()
        self.events = EventListener(LIBRESPOT_WS, self._on_ws_update)
        
        # Managers
        self.sleep_manager = SleepManager()
        self.carousel = SmoothCarousel()
        self.play_timer = PlayTimer()
        self.perf_monitor = PerformanceMonitor()
        
        # State
        self.now_playing = NowPlaying()
        self.selected_index = 0
        self.connected = self.mock_mode
        self._connection_fail_count = 0  # Track consecutive failures
        self._connection_grace_threshold = 3  # Failures before showing disconnected
        self.needs_refresh = True
        self.running = True
        
        # TempItem and delete mode
        self.temp_item: Optional[CatalogItem] = None
        self.delete_mode_id: Optional[str] = None
        self.saving = False
        self.deleting = False
        
        # Volume state
        self.volume_index = 1  # Start at 'low' (75%)
        self._last_synced_volume = -1
        
        # Interaction tracking
        self.user_interacting = False
        self.last_context_uri: Optional[str] = None
        self.last_progress_save = 0
        self.last_saved_track_uri: Optional[str] = None
        self.last_user_play_time = 0
        self.last_user_play_uri: Optional[str] = None
        
        # Non-blocking play request handling
        self._play_lock = threading.Lock()
        self._play_in_progress = False
        self._pending_play: Optional[tuple] = None  # (uri, from_beginning)
        
        # Mock playback state
        self.mock_playing = False
        self.mock_position = 0
        self.mock_duration = 180000
        
        # Initialize carousel
        self._update_carousel_max_index()
    
    def _load_icons(self) -> dict:
        """Load icon images."""
        icons = {}
        icon_files = {
            'play': 'play-fill.png',
            'pause': 'pause-fill.png',
            'prev': 'skip-back-fill.png',
            'next': 'skip-forward-fill.png',
            'volume_none': 'speaker-none-fill.png',
            'volume_low': 'speaker-low-fill.png',
            'volume_high': 'speaker-high-fill.png',
            'plus': 'plus-circle-fill.png',
            'minus': 'minus-circle-fill.png',
        }
        for name, filename in icon_files.items():
            try:
                icons[name] = pygame.image.load(ICONS_DIR / filename).convert_alpha()
            except Exception as e:
                logger.warning(f'Failed to load icon {filename}: {e}')
        return icons
    
    def _log_video_info(self):
        """Log video driver and display info."""
        video_driver = os.environ.get('SDL_VIDEODRIVER', 'default')
        actual_driver = pygame.display.get_driver()
        info = pygame.display.Info()
        
        logger.info(f'Display: {actual_driver} (requested: {video_driver})')
        logger.info(f'Resolution: {info.current_w}x{info.current_h}')
        
        # Check for Raspberry Pi
        if os.path.exists('/proc/device-tree/model'):
            try:
                with open('/proc/device-tree/model', 'r') as f:
                    pi_model = f.read().strip().replace('\x00', '')
                logger.info(f'Device: {pi_model}')
                
                if actual_driver not in ('kmsdrm', 'KMSDRM'):
                    logger.warning('Consider enabling KMS/DRM for GPU acceleration')
            except Exception:
                pass
    
    def _on_ws_update(self):
        """Called when WebSocket receives an event."""
        self.needs_refresh = True
        logger.debug(f'WebSocket event, context: {self.events.context_uri}')
    
    @property
    def display_items(self) -> List[CatalogItem]:
        """Return catalog items + tempItem if present."""
        items = self.catalog_manager.items
        if self.temp_item:
            return items + [self.temp_item]
        return items
    
    def _update_carousel_max_index(self):
        """Update carousel max index when items change."""
        self.carousel.max_index = max(0, len(self.display_items) - 1)
    
    def start(self):
        """Start the application."""
        logger.info('Starting Berry...')
        
        # Pre-load images
        self.image_cache.preload_catalog(self.catalog_manager.items)
        
        if not self.mock_mode:
            self.events.start()
            self.catalog_manager.cleanup_unused_images()
            
            # Start status polling
            threading.Thread(target=self._poll_status, daemon=True).start()
            logger.info(f'Polling {LIBRESPOT_URL}')
        else:
            logger.info('Running in MOCK MODE')
        
        # Main loop
        while self.running:
            is_animating = not self.carousel.settled or self.touch.dragging
            target_fps = 60 if is_animating else 30
            dt = self.clock.tick(target_fps) / 1000.0
            
            self._handle_events()
            self._update(dt)
            dirty_rects = self._draw()
            
            if dirty_rects:
                pygame.display.update(dirty_rects)
            else:
                pygame.display.flip()
            
            self.perf_monitor.update(dt, is_animating)
        
        self.events.stop()
        pygame.quit()
        logger.info('Berry stopped')
    
    def _poll_status(self):
        """Poll librespot status in background."""
        while self.running:
            try:
                self._refresh_status()
            except Exception as e:
                # Handle connection errors with grace period
                self._connection_fail_count += 1
                if self._connection_fail_count >= self._connection_grace_threshold:
                    if self.connected:  # Only log once
                        logger.error(f'Status poll error: {e}')
                    self.connected = False
            time.sleep(1)
    
    def _refresh_status(self):
        """Refresh playback status from librespot."""
        status = self.api.status()
        
        # Determine connection with grace period
        has_connection = status is not None or self.api.is_connected()
        if has_connection:
            self._connection_fail_count = 0
            self.connected = True
        else:
            self._connection_fail_count += 1
            if self._connection_fail_count >= self._connection_grace_threshold:
                if self.connected:  # Only log once
                    logger.warning(f'Connection lost after {self._connection_fail_count} failures')
                self.connected = False
        
        if status and isinstance(status, dict):
            track = status.get('track') or {}
            if not isinstance(track, dict):
                track = {}
            
            playing = not status.get('stopped', True) and not status.get('paused', False)
            
            self.now_playing = NowPlaying(
                playing=playing,
                paused=status.get('paused', False),
                stopped=status.get('stopped', True),
                context_uri=self.events.context_uri,
                track_name=track.get('name'),
                track_artist=', '.join(track.get('artist_names', [])) if track.get('artist_names') else None,
                track_album=track.get('album_name'),
                track_cover=track.get('album_cover_url'),
                position=track.get('position', 0),
                duration=track.get('duration', 0),
            )
            
            # Sync volume
            spotify_volume = status.get('volume')
            if spotify_volume is not None:
                self._sync_volume_from_spotify(spotify_volume)
            
            # Update tempItem
            self._update_temp_item()
            
            # Autoplay detection
            self._check_autoplay()
            
            # Wake from sleep when music starts
            if playing and self.sleep_manager.is_sleeping:
                self.sleep_manager.wake_up()
        else:
            self.now_playing = NowPlaying()
        
        self.needs_refresh = False
    
    def _check_autoplay(self):
        """Detect autoplay and clear progress when context finishes."""
        new_context = self.now_playing.context_uri
        old_context = self.last_context_uri
        
        if (old_context and new_context and 
            old_context != new_context and 
            self.now_playing.playing):
            
            recent_user_action = time.time() - self.last_user_play_time < 5
            expected_context = new_context == self.last_user_play_uri
            
            if not recent_user_action and not expected_context:
                logger.info(f'Context finished: {old_context}')
                self.catalog_manager.clear_progress(old_context)
    
    def _update_temp_item(self):
        """Update tempItem based on current playback context."""
        context_uri = self.now_playing.context_uri
        
        if not context_uri:
            if self.temp_item:
                self.temp_item = None
                self._update_carousel_max_index()
                self.renderer.invalidate()
            return
        
        # Check if in catalog
        in_catalog = any(item.uri == context_uri for item in self.catalog_manager.items)
        if in_catalog:
            if self.temp_item:
                self.temp_item = None
                self._update_carousel_max_index()
                self.renderer.invalidate()
            return
        
        # Create/update tempItem
        is_playlist = 'playlist' in context_uri
        collected_covers = self.catalog_manager.get_collected_covers(context_uri) if is_playlist else None
        
        current_cover_count = len(self.temp_item.images or []) if self.temp_item else 0
        new_cover_count = len(collected_covers or [])
        
        needs_update = (
            not self.temp_item or 
            self.temp_item.uri != context_uri or
            new_cover_count > current_cover_count
        )
        
        if needs_update:
            self.temp_item = CatalogItem(
                id='temp',
                uri=context_uri,
                name=self.now_playing.track_album or ('Playlist' if is_playlist else 'Album'),
                type='playlist' if is_playlist else 'album',
                artist=self.now_playing.track_artist,
                image=self.now_playing.track_cover,
                images=collected_covers,
                is_temp=True
            )
            self._update_carousel_max_index()
            self.renderer.invalidate()
            logger.info(f'TempItem: {self.temp_item.name}')
    
    def _handle_events(self):
        """Handle pygame events."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if self.sleep_manager.is_sleeping:
                    self.sleep_manager.wake_up()
                    continue
                self.sleep_manager.reset_timer()
                self._handle_touch_down(event.pos)
            
            elif event.type == pygame.KEYDOWN:
                if self.sleep_manager.is_sleeping:
                    self.sleep_manager.wake_up()
                    continue
                self.sleep_manager.reset_timer()
                self._handle_key(event.key)
            
            elif event.type == pygame.MOUSEMOTION:
                if self.touch.dragging:
                    self.sleep_manager.reset_timer()
                    self.touch.on_move(event.pos)
            
            elif event.type == pygame.MOUSEBUTTONUP:
                if not self.sleep_manager.is_sleeping:
                    self._handle_touch_up(event.pos)
    
    def _handle_key(self, key):
        """Handle keyboard input."""
        if key == pygame.K_ESCAPE:
            self.running = False
        elif key == pygame.K_LEFT:
            self._navigate(-1)
        elif key == pygame.K_RIGHT:
            self._navigate(1)
        elif key == pygame.K_SPACE or key == pygame.K_RETURN:
            self._toggle_play()
        elif key == pygame.K_n:
            self.api.next()
        elif key == pygame.K_p:
            self.api.prev()
    
    def _handle_touch_down(self, pos):
        """Handle touch/mouse down."""
        x, y = pos
        
        # Check button clicks
        if self._check_button_click(pos):
            return
        
        # Cancel delete mode
        if self.delete_mode_id:
            self.delete_mode_id = None
            self.renderer.invalidate()
        
        # Handle carousel swipes
        if CAROUSEL_Y <= y <= CAROUSEL_Y + COVER_SIZE + 50:
            self.touch.on_down(pos)
            self.user_interacting = True
            self.play_timer.cancel()
        else:
            self._handle_button_tap(pos)
    
    def _check_button_click(self, pos) -> bool:
        """Check if click is on add/delete button."""
        x, y = pos
        
        if self.renderer.add_button_rect:
            bx, by, bw, bh = self.renderer.add_button_rect
            if bx <= x <= bx + bw and by <= y <= by + bh:
                self._save_temp_item()
                return True
        
        if self.renderer.delete_button_rect:
            bx, by, bw, bh = self.renderer.delete_button_rect
            if bx <= x <= bx + bw and by <= y <= by + bh:
                self._delete_current_item()
                return True
        
        return False
    
    def _handle_touch_up(self, pos):
        """Handle touch/mouse up."""
        if not self.touch.dragging:
            return
        
        drag_index_offset = -self.touch.drag_offset / (COVER_SIZE + COVER_SPACING)
        visual_position = self.selected_index + drag_index_offset
        
        action, velocity = self.touch.on_up(pos)
        self.carousel.scroll_x = visual_position
        
        x, y = pos
        center_x = SCREEN_WIDTH // 2
        
        if action in ('left', 'right'):
            # Calculate target based on position + velocity
            abs_vel = abs(velocity)
            velocity_bonus = 0 if abs_vel < 1.0 else (1 if abs_vel < 2.0 else (2 if abs_vel < 3.5 else 3))
            
            base_target = round(visual_position)
            target = base_target + velocity_bonus if velocity < 0 else base_target - velocity_bonus
            
            # Clamp
            max_jump = 5
            target = max(self.selected_index - max_jump, min(target, self.selected_index + max_jump))
            target = max(0, min(target, len(self.display_items) - 1))
            
            self._snap_to(target)
        elif action == 'tap':
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
        
        right_cover_edge = center_x + (COVER_SIZE + COVER_SPACING) + COVER_SIZE_SMALL // 2
        vol_x = right_cover_edge - BTN_SIZE // 2
        
        if CONTROLS_Y - PLAY_BTN_SIZE <= y <= CONTROLS_Y + PLAY_BTN_SIZE:
            if center_x - btn_spacing - BTN_SIZE <= x <= center_x - btn_spacing + BTN_SIZE:
                self.api.prev()
            elif center_x - PLAY_BTN_SIZE <= x <= center_x + PLAY_BTN_SIZE:
                self._toggle_play()
            elif center_x + btn_spacing - BTN_SIZE <= x <= center_x + btn_spacing + BTN_SIZE:
                self.api.next()
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
            
            item = items[target_index]
            if not item.is_temp and not self._is_item_playing(item):
                self.play_timer.start(item)
            else:
                self.play_timer.cancel()
        else:
            self.carousel.set_target(target_index)
    
    def _navigate(self, direction: int):
        """Navigate carousel."""
        items = self.display_items
        if not items:
            return
        
        new_index = max(0, min(self.selected_index + direction, len(items) - 1))
        self._snap_to(new_index)
    
    def _is_item_playing(self, item: CatalogItem) -> bool:
        """Check if an item is currently playing."""
        return item.uri == self.now_playing.context_uri
    
    def _toggle_play(self):
        """Toggle play/pause."""
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
            logger.info('Pausing...')
            self.api.pause()
        elif self.now_playing.paused:
            logger.info('Resuming...')
            self.api.resume()
        elif items:
            item = items[self.selected_index]
            logger.info(f'Playing {item.name}')
            self._play_item(item.uri)
    
    def _play_item(self, uri: str, from_beginning: bool = False):
        """Queue a play request (non-blocking). Only the latest request is executed."""
        self.last_user_play_time = time.time()
        self.last_user_play_uri = uri
        
        # Save current progress before switching
        if self.now_playing.context_uri and self.now_playing.context_uri != uri:
            self._save_playback_progress()
        
        with self._play_lock:
            if self._play_in_progress:
                # Replace pending request with the latest one
                self._pending_play = (uri, from_beginning)
                logger.debug(f'Queued play request: {uri}')
                return
            
            self._play_in_progress = True
        
        # Execute in background thread
        threading.Thread(
            target=self._execute_play,
            args=(uri, from_beginning),
            daemon=True
        ).start()
    
    def _execute_play(self, uri: str, from_beginning: bool):
        """Execute the play request in background thread."""
        try:
            # Check for saved progress
            skip_to_uri = None
            saved_progress = None
            if not from_beginning:
                saved_progress = self.catalog_manager.get_progress(uri)
                if saved_progress:
                    skip_to_uri = saved_progress.get('uri')
            
            success = self.api.play(uri, skip_to_uri=skip_to_uri)
            
            # Seek to saved position
            if success and saved_progress and saved_progress.get('position', 0) > 0:
                time.sleep(0.5)
                position = saved_progress['position']
                if self.api.seek(position):
                    logger.info(f'Seeked to {position // 1000}s')
        finally:
            # Check for pending request
            with self._play_lock:
                self._play_in_progress = False
                pending = self._pending_play
                self._pending_play = None
            
            # Execute pending request if any
            if pending:
                logger.debug(f'Executing queued request: {pending[0]}')
                self._play_item(pending[0], pending[1])
    
    def _toggle_volume(self):
        """Toggle between volume levels."""
        self.volume_index = (self.volume_index + 1) % len(VOLUME_LEVELS)
        level = VOLUME_LEVELS[self.volume_index]['level']
        
        logger.info(f'Volume: {level}%')
        self.api.set_volume(level)
        self._set_system_volume(level)
    
    def _set_system_volume(self, level: int):
        """Set the Pi's ALSA system volume."""
        if sys.platform != 'linux':
            return
        try:
            subprocess.run(['amixer', 'set', 'Master', f'{level}%'],
                          capture_output=True, check=True)
        except Exception as e:
            logger.warning(f'Could not set system volume: {e}')
    
    def _sync_volume_from_spotify(self, spotify_volume: int):
        """Sync system volume when Spotify volume changes externally."""
        if spotify_volume == self._last_synced_volume:
            return
        
        self._last_synced_volume = spotify_volume
        self._set_system_volume(spotify_volume)
        
        # Update icon
        closest_index = min(range(len(VOLUME_LEVELS)), 
                           key=lambda i: abs(VOLUME_LEVELS[i]['level'] - spotify_volume))
        if closest_index != self.volume_index:
            self.volume_index = closest_index
            logger.debug(f'Volume synced: {spotify_volume}%')
    
    def _save_temp_item(self):
        """Save the current temp item to catalog."""
        if not self.temp_item or self.saving:
            return
        
        self.saving = True
        logger.info(f'Saving: {self.temp_item.name}')
        
        item_data = {
            'type': self.temp_item.type,
            'uri': self.temp_item.uri,
            'name': self.temp_item.name,
            'artist': self.temp_item.artist,
            'image': self.temp_item.image,
        }
        
        success = self.catalog_manager.save_item(item_data)
        
        if success:
            self.catalog_manager.load()
            self._update_carousel_max_index()
            self.image_cache.preload_catalog(self.catalog_manager.items)
            self.temp_item = None
            self.renderer.invalidate()
        
        self.saving = False
    
    def _delete_current_item(self):
        """Delete the current item from catalog."""
        if not self.delete_mode_id or self.deleting:
            return
        
        self.deleting = True
        item_id = self.delete_mode_id
        old_index = self.selected_index
        
        item = next((i for i in self.catalog_manager.items if i.id == item_id), None)
        if item:
            logger.info(f'Deleting: {item.name}')
        
        success = self.catalog_manager.delete_item(item_id)
        
        if success:
            self.catalog_manager.load()
            self._update_carousel_max_index()
            
            new_index = max(0, old_index - 1)
            if self.display_items:
                new_index = min(new_index, len(self.display_items) - 1)
                self.selected_index = new_index
                self.carousel.scroll_x = float(new_index)
                self.carousel.set_target(new_index)
                
                new_item = self.display_items[new_index]
                if not new_item.is_temp:
                    self._play_item(new_item.uri)
        
        self.delete_mode_id = None
        self.deleting = False
        self.renderer.invalidate()
    
    def _trigger_delete_mode(self):
        """Trigger delete mode for the currently selected item."""
        items = self.display_items
        if not items or self.selected_index >= len(items):
            return
        
        item = items[self.selected_index]
        if item.is_temp:
            return
        
        logger.info(f'Delete mode: {item.name}')
        self.delete_mode_id = item.id
        self.renderer.invalidate()
    
    def _save_playback_progress(self):
        """Save current playback position."""
        if self.mock_mode:
            return
        
        try:
            status = self.api.status()
            if not status or not status.get('track'):
                return
            
            context_uri = status.get('context_uri') or self.now_playing.context_uri
            if not context_uri:
                return
            
            track = status['track']
            self.catalog_manager.save_progress(
                context_uri,
                track.get('uri'),
                track.get('position', 0),
                track.get('name'),
                ', '.join(track.get('artist_names', []))
            )
            
            self.last_saved_track_uri = track.get('uri')
            self.last_progress_save = time.time()
            
        except Exception as e:
            logger.warning(f'Error saving progress: {e}')
    
    def _sync_to_playing(self):
        """Sync carousel to currently playing item."""
        items = self.display_items
        if not items or self.user_interacting:
            return
        
        if self.play_timer.item or not self.carousel.settled:
            return
        
        if self.play_timer.is_in_cooldown():
            return
        
        context_uri = self.now_playing.context_uri
        if not context_uri or context_uri == self.last_context_uri:
            return
        
        if context_uri == self.play_timer.last_played_uri:
            self.play_timer.last_played_uri = None
            self.last_context_uri = context_uri
            return
        
        playing_index = next((i for i, item in enumerate(items) if item.uri == context_uri), None)
        if playing_index is None:
            return
        
        if playing_index != self.selected_index:
            logger.info(f'Syncing to: {items[playing_index].name}')
            self.selected_index = playing_index
            self.carousel.set_target(playing_index)
        
        self.last_context_uri = context_uri
    
    def _update(self, dt: float):
        """Update application state."""
        items = self.display_items
        if items:
            self.selected_index = max(0, min(self.selected_index, len(items) - 1))
        
        # Update carousel
        was_animating = not self.carousel.settled
        self.carousel.update(dt)
        
        if was_animating and self.carousel.settled:
            new_index = self.carousel.target_index
            if new_index != self.selected_index and items:
                self.selected_index = new_index
                if new_index < len(items):
                    item = items[new_index]
                    if not item.is_temp and not self._is_item_playing(item):
                        self.play_timer.start(item)
        
        # Check long press for delete mode
        if self.touch.check_long_press():
            self._trigger_delete_mode()
        
        # Update interaction state
        self.user_interacting = (
            self.touch.dragging or 
            not self.carousel.settled or 
            self.play_timer.item is not None
        )
        
        # Check play timer
        item_to_play = self.play_timer.check()
        if item_to_play:
            logger.info(f'Auto-playing: {item_to_play.name}')
            self._play_item(item_to_play.uri)
        
        # Sync to playing
        self._sync_to_playing()
        
        # Mock mode progress
        if self.mock_mode and self.mock_playing:
            self.mock_position += int(dt * 1000)
            if self.mock_position >= self.mock_duration:
                self.mock_position = 0
            self.now_playing.position = self.mock_position
        
        # Periodic progress save
        if (self.now_playing.playing and 
            not self.mock_mode and
            time.time() - self.last_progress_save > PROGRESS_SAVE_INTERVAL):
            self._save_playback_progress()
        
        # Collect playlist covers
        if (self.now_playing.playing and 
            'playlist' in (self.now_playing.context_uri or '')):
            self.catalog_manager.collect_cover_for_playlist(
                self.now_playing.context_uri,
                self.now_playing.track_cover
            )
        
        # Check sleep
        self.sleep_manager.check_sleep(self.now_playing.playing)
    
    def _draw(self):
        """Draw the UI."""
        return self.renderer.draw(
            items=self.display_items,
            selected_index=self.selected_index,
            now_playing=self.now_playing,
            scroll_x=self.carousel.scroll_x,
            drag_offset=self.touch.drag_offset,
            dragging=self.touch.dragging,
            is_sleeping=self.sleep_manager.is_sleeping,
            connected=self.connected,
            volume_index=self.volume_index,
            delete_mode_id=self.delete_mode_id,
        )

