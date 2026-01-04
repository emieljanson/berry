"""
Berry Application - Main application class.
"""
import os
import sys
import time
import signal
import logging
import threading
import subprocess
from typing import Optional, List

import numpy as np
import pygame

from .config import (
    SCREEN_WIDTH, SCREEN_HEIGHT, COLORS,
    LIBRESPOT_URL, LIBRESPOT_WS, 
    CATALOG_PATH, IMAGES_DIR, ICONS_DIR,
    MOCK_MODE, VOLUME_LEVELS,
    COVER_SIZE, COVER_SIZE_SMALL, COVER_SPACING,
    CAROUSEL_Y, CONTROLS_Y, BTN_SIZE, PLAY_BTN_SIZE, BTN_SPACING,
    PROGRESS_SAVE_INTERVAL,
    has_spotify_credentials,
)
from .models import CatalogItem, NowPlaying
from .api import LibrespotAPI, CatalogManager
from .handlers import TouchHandler, EventListener
from .managers import SleepManager, SmoothCarousel, PlayTimer, PerformanceMonitor, AutoPauseManager
from .ui import ImageCache, Renderer

logger = logging.getLogger(__name__)


class Berry:
    """Main Berry application."""
    
    def __init__(self, fullscreen: bool = False):
        # Try GPU-accelerated driver first on Raspberry Pi
        self._setup_video_driver()
        
        pygame.init()
        pygame.display.set_caption('Berry')
        
        # Initialize display and components
        self._init_display(fullscreen)
        self._init_components()
    
    def _check_kms_available(self) -> bool:
        """Check if KMS/DRM is likely configured on the system."""
        # Check for DRI devices (KMS/DRM creates these)
        if os.path.exists('/dev/dri'):
            try:
                dri_devices = os.listdir('/dev/dri')
                # Should have at least card0 or renderD128
                if any(dev.startswith(('card', 'renderD')) for dev in dri_devices):
                    return True
            except OSError:
                pass
        
        # Check if GL driver is configured (check for vc4-kms-v3d overlay)
        try:
            if os.path.exists('/boot/config.txt'):
                with open('/boot/config.txt', 'r') as f:
                    config = f.read()
                    # Check for KMS-related overlays
                    if 'dtoverlay=vc4-kms-v3d' in config or 'dtoverlay=vc4-kms-dsi-7inch' in config:
                        return True
        except (OSError, IOError):
            pass
        
        return False
    
    def _setup_video_driver(self):
        """Configure optimal video driver for the platform."""
        # Skip if already set
        if os.environ.get('SDL_VIDEODRIVER'):
            return
        
        # Only try kmsdrm on Raspberry Pi
        if not os.path.exists('/proc/device-tree/model'):
            return
        
        # Check if KMS/DRM is likely configured
        kms_configured = self._check_kms_available()
        
        # Try kmsdrm for GPU acceleration
        os.environ['SDL_VIDEODRIVER'] = 'kmsdrm'
        try:
            pygame.display.init()
            pygame.display.quit()  # Success - will reinit in pygame.init()
            logger.info('Using kmsdrm (GPU accelerated)')
        except pygame.error as e:
            # Fall back to default driver
            del os.environ['SDL_VIDEODRIVER']
            error_msg = str(e) if e else 'unknown error'
            logger.warning(f'kmsdrm driver failed: {error_msg}')
            
            if not kms_configured:
                logger.warning('=' * 60)
                logger.warning('GPU ACCELERATION NOT AVAILABLE')
                logger.warning('=' * 60)
                logger.warning('To enable GPU acceleration on Raspberry Pi:')
                logger.warning('  1. Run: sudo raspi-config')
                logger.warning('  2. Navigate to: Advanced Options > GL Driver')
                logger.warning('  3. Select: G1 GL (Full KMS)')
                logger.warning('  4. Reboot the Pi')
                logger.warning('=' * 60)
            else:
                logger.warning('KMS/DRM appears configured but kmsdrm driver failed.')
                logger.warning('This may indicate a driver compatibility issue.')
            logger.info('Falling back to default driver (software rendering)')
    
    def _init_display(self, fullscreen: bool):
        """Initialize the display with optimal settings."""
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
    
    def _init_components(self):
        """Initialize all application components."""
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
        self.events = EventListener(LIBRESPOT_WS, self._on_ws_update, self._on_ws_reconnect)
        
        # Managers
        self.sleep_manager = SleepManager()
        self.carousel = SmoothCarousel()
        self.play_timer = PlayTimer()
        self.perf_monitor = PerformanceMonitor()
        self.auto_pause = AutoPauseManager(
            on_pause=lambda: threading.Thread(target=self.api.pause, daemon=True).start(),
            get_volume=lambda: VOLUME_LEVELS[self.volume_index]['level']
        )
        
        # State (with thread-safe now_playing and connected)
        self._now_playing = NowPlaying()
        self._now_playing_lock = threading.Lock()
        self._connected = self.mock_mode
        self._connected_lock = threading.Lock()
        self.selected_index = 0
        self._connection_fail_count = 0  # Track consecutive failures
        self._connection_grace_threshold = 3  # Failures before showing disconnected
        self.needs_refresh = True
        self.running = True
        
        # Setup state (first-time Spotify connection)
        self.needs_setup = not has_spotify_credentials()
        self._last_credentials_check = 0
        
        # TempItem and delete mode
        self.temp_item: Optional[CatalogItem] = None
        self.delete_mode_id: Optional[str] = None
        self.saving = False
        self.deleting = False
        
        # Volume state with ownership model
        # 'berry': Spotify at 100%, Pi controls volume
        # 'spotify': Pi at 100%, Spotify controls volume
        self._volume_mode = 'berry'
        self.volume_index = 1  # Start at 'normal' (55%)
        self._berry_volume_index = 1  # Remember Berry's choice when in Spotify mode
        self._spotify_volume_initialized = False  # Set Spotify to 100% on first play
        
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
        
        # Navigation pause state (pause when swiping away from playing item)
        self._paused_for_navigation = False
        self._paused_context_uri: Optional[str] = None
        self._loading_start_time: Optional[float] = None
        self._is_loading: bool = False
        self._should_show_loading: bool = False
        
        # Button debouncing and feedback
        self._last_action_time = 0
        self._action_debounce = 0.3  # 300ms between button actions
        self._pressed_button: Optional[str] = None  # 'play', 'prev', 'next', 'volume'
        self._pressed_time = 0
        self._volume_change_time = 0  # Track local volume changes for sync cooldown
        
        # Audio feedback (click sound)
        self._click_sound = self._create_click_sound()
        
        # Mock playback state
        self.mock_playing = False
        self.mock_position = 0
        
        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        self.mock_duration = 180000
        
        # Performance logging
        self._last_fps_log = time.time()
        self._fps_log_interval = 30  # Log FPS every 30 seconds
        
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
                logger.warning(f'Failed to load icon {filename}: {e}', exc_info=True)
        return icons
    
    def _create_click_sound(self):
        """Create a short click sound for button feedback."""
        try:
            # Initialize mixer if not already done
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=1)
            
            # Generate a short click (15ms, 600Hz sine wave with quick fade)
            duration = 0.015
            sample_rate = 22050
            t = np.linspace(0, duration, int(sample_rate * duration), False)
            wave = np.sin(600 * 2 * np.pi * t) * 0.25  # 600Hz, 25% volume
            fade = np.linspace(1, 0, len(wave))  # Quick fade out
            wave = (wave * fade * 32767).astype(np.int16)
            
            # Create stereo sound (duplicate mono to both channels)
            stereo_wave = np.column_stack([wave, wave])
            return pygame.sndarray.make_sound(stereo_wave)
        except Exception as e:
            logger.debug(f'Could not create click sound: {e}')  # Not critical, debug level
            return None
    
    def _play_click(self):
        """Play the click sound if available."""
        if self._click_sound:
            try:
                self._click_sound.play()
            except Exception:
                pass  # Ignore audio errors
    
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
                
                # Only show warning if not using GPU acceleration
                if actual_driver not in ('kmsdrm', 'KMSDRM'):
                    kms_available = self._check_kms_available()
                    if not kms_available:
                        logger.debug('KMS/DRM not detected - GPU acceleration unavailable')
                    else:
                        logger.debug('KMS/DRM detected but not using kmsdrm driver')
            except Exception:
                pass
    
    def _on_ws_update(self):
        """Called when WebSocket receives an event."""
        self.needs_refresh = True
        logger.debug(f'WebSocket event, context: {self.events.context_uri}')
    
    def _on_ws_reconnect(self):
        """Called when WebSocket reconnects after disconnect."""
        logger.info('WebSocket reconnected - refreshing state')
        self._connection_fail_count = 0
        self.connected = True
        self.needs_refresh = True
        # Force immediate status refresh
        threading.Thread(target=self._refresh_status, daemon=True).start()
    
    @property
    def display_items(self) -> List[CatalogItem]:
        """Return catalog items + tempItem if present."""
        items = self.catalog_manager.items
        if self.temp_item:
            return items + [self.temp_item]
        return items
    
    @property
    def now_playing(self) -> NowPlaying:
        """Thread-safe getter for now_playing state."""
        with self._now_playing_lock:
            return self._now_playing
    
    @now_playing.setter
    def now_playing(self, value: NowPlaying):
        """Thread-safe setter for now_playing state."""
        with self._now_playing_lock:
            self._now_playing = value
    
    @property
    def connected(self) -> bool:
        """Thread-safe getter for connected state."""
        with self._connected_lock:
            return self._connected
    
    @connected.setter
    def connected(self, value: bool):
        """Thread-safe setter for connected state."""
        with self._connected_lock:
            self._connected = value
    
    def _update_carousel_max_index(self):
        """Update carousel max index when items change."""
        self.carousel.max_index = max(0, len(self.display_items) - 1)
    
    def _handle_signal(self, signum, frame):
        """Handle SIGTERM/SIGINT for graceful shutdown."""
        sig_name = 'SIGTERM' if signum == signal.SIGTERM else 'SIGINT'
        logger.info(f'Received {sig_name}, shutting down...')
        self.running = False
    
    def start(self):
        """Start the application."""
        logger.info('Starting Berry...')
        
        if self.needs_setup:
            logger.info('Setup mode: waiting for Spotify connection')
            logger.info('Connect to "Berry" in your Spotify app')
        
        # Pre-load images
        self.image_cache.preload_catalog(self.catalog_manager.items)
        
        if not self.mock_mode:
            self.events.start()
            self.catalog_manager.cleanup_unused_images()
            
            # Set Spotify volume to 100% once at startup (we control volume via system audio)
            self._init_volume()  # Set system volume (no thread needed)
            
            # Start status polling
            threading.Thread(target=self._poll_status, daemon=True).start()
            logger.info(f'Polling {LIBRESPOT_URL}')
            
            # Force initial connection check (don't wait for first poll interval)
            threading.Thread(target=self._initial_connect, daemon=True).start()
        else:
            logger.info('Running in MOCK MODE')
        
        logger.info('Entering main loop...')
        dt = 1.0 / 60  # Initial delta time
        
        # Main loop
        while self.running:
            # Sleep mode: minimal CPU usage, only check for wake events
            if self.sleep_manager.is_sleeping:
                self.clock.tick(1)  # 1 FPS during sleep
                self._handle_events()
                continue
            
            self._handle_events()
            self._update(dt)
            dirty_rects = self._draw()
            
            if dirty_rects:
                pygame.display.update(dirty_rects)
            else:
                pygame.display.flip()
            
            # Dynamic FPS based on current activity (after update/draw):
            # - 60 FPS: carousel animation, touch dragging, loading spinner
            #           (Rendering happens first, clock.tick limits upward only)
            # - 10 FPS: music playing (progress bar updates)
            # - 5 FPS: idle (fully static screen)
            is_animating = not self.carousel.settled or self.touch.dragging
            if is_animating or self._is_loading:
                target_fps = 60  # Smooth animations and spinner
            elif self.now_playing.playing:
                target_fps = 10
            else:
                target_fps = 5
            
            dt = self.clock.tick(target_fps) / 1000.0
            
            # Log frame spikes (>20% over target, minimum 100ms)
            target_frame_time = 1.0 / target_fps
            spike_threshold = max(0.1, target_frame_time * 1.2)
            if dt > spike_threshold:
                logger.warning(f'Frame spike: {dt*1000:.0f}ms (target: {target_fps} FPS)')
            
            self.perf_monitor.update(dt, is_animating)
            
            # Periodic FPS logging
            now = time.time()
            if now - self._last_fps_log >= self._fps_log_interval:
                self._last_fps_log = now
                avg_fps = self.perf_monitor.current_fps
                
                # Determine current target FPS for warning threshold
                log_is_animating = not self.carousel.settled or self.touch.dragging
                if log_is_animating or self._is_loading:
                    log_target_fps = 60
                elif self.now_playing.playing:
                    log_target_fps = 10
                else:
                    log_target_fps = 5
                
                logger.info(f'FPS: {avg_fps:.1f} | connected={self.connected} | playing={self.now_playing.playing} | loading={self._is_loading} | target={log_target_fps}')
                
                # Warn if FPS is significantly below target:
                # - Target 60 FPS (animations): warn if < 30 FPS (50% below)
                # - Target 10 FPS (playing): warn if < 8 FPS (20% below)
                # - Target 5 FPS (idle): warn if < 4 FPS (20% below)
                if not self.sleep_manager.is_sleeping:
                    if log_target_fps == 60 and avg_fps < 30:
                        logger.warning(f'Low FPS during animation: {avg_fps:.1f} (target: 60 FPS)')
                    elif log_target_fps == 10 and avg_fps < 8:
                        logger.warning(f'Low FPS while playing: {avg_fps:.1f} (target: 10 FPS)')
                    elif log_target_fps == 5 and avg_fps < 4:
                        logger.warning(f'Low FPS while idle: {avg_fps:.1f} (target: 5 FPS)')
        
        # Save progress before shutdown
        logger.info('Shutting down...')
        self._save_progress_on_shutdown()
        
        self.events.stop()
        pygame.quit()
        logger.info('Berry stopped')
    
    def _poll_status(self):
        """Poll librespot status in background."""
        was_fast_polling = False
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
            
            # Poll faster when disconnected for quicker recovery
            is_fast_polling = not self.connected
            if is_fast_polling != was_fast_polling:
                if is_fast_polling:
                    logger.debug('Fast polling mode (disconnected)')
                else:
                    logger.debug('Normal polling mode (connected)')
                was_fast_polling = is_fast_polling
            
            poll_interval = 0.5 if is_fast_polling else 1.0
            time.sleep(poll_interval)
    
    def _refresh_status(self):
        """Refresh playback status from librespot."""
        status = self.api.status()
        was_connected = self.connected
        
        # Determine connection with grace period
        has_connection = status is not None or self.api.is_connected()
        if has_connection:
            if self._connection_fail_count > 0:
                logger.debug(f'Connection recovered after {self._connection_fail_count} failures')
            self._connection_fail_count = 0
            self.connected = True
        else:
            self._connection_fail_count += 1
            if self._connection_fail_count >= self._connection_grace_threshold:
                self.connected = False
        
        # Log connection state changes
        if was_connected != self.connected:
            if self.connected:
                logger.info(f'CONNECTION RESTORED (was disconnected)')
            else:
                logger.warning(f'CONNECTION LOST after {self._connection_fail_count} failures')
            logger.info(f'  fail_count={self._connection_fail_count}, status={status is not None}')
        
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
            
            # Handle volume ownership
            spotify_volume = status.get('volume')
            if spotify_volume is not None:
                self._handle_spotify_volume_change(spotify_volume)
            
            # Update tempItem
            self._update_temp_item()
            
            # Autoplay detection
            self._check_autoplay()
            
            # Wake from sleep when music starts
            if playing and self.sleep_manager.is_sleeping:
                self.sleep_manager.wake_up()
                self._on_wake()
            
            # Auto-pause check (pauses after 30min in same context)
            if playing:
                self.auto_pause.on_play(self.now_playing.context_uri)
                self.auto_pause.check(is_playing=True)
            elif status.get('paused') or status.get('stopped'):
                self.auto_pause.on_stop()
        else:
            self.now_playing = NowPlaying()
            self.auto_pause.on_stop()
        
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
                # Cleanup temp images when temp item is removed
                self.catalog_manager.cleanup_temp_images()
                self._update_carousel_max_index()
                self.renderer.invalidate()
            return
        
        # Check if in catalog
        in_catalog = any(item.uri == context_uri for item in self.catalog_manager.items)
        if in_catalog:
            if self.temp_item:
                self.temp_item = None
                # Cleanup temp images when temp item is removed
                self.catalog_manager.cleanup_temp_images()
                self._update_carousel_max_index()
                self.renderer.invalidate()
            return
        
        # Create/update tempItem
        is_playlist = 'playlist' in context_uri
        
        needs_update = (
            not self.temp_item or 
            self.temp_item.uri != context_uri
        )
        
        if needs_update:
            # Download temp image if URL provided
            image_url = self.now_playing.track_cover
            local_image = None
            if image_url and image_url.startswith('http'):
                local_image = self.catalog_manager.download_temp_image(image_url)
            
            self.temp_item = CatalogItem(
                id='temp',
                uri=context_uri,
                name=self.now_playing.track_album or ('Playlist' if is_playlist else 'Album'),
                type='playlist' if is_playlist else 'album',
                artist=self.now_playing.track_artist,
                image=local_image or image_url,  # Use local image if available, else URL
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
                    self._on_wake()
                    continue
                self.sleep_manager.reset_timer()
                self._handle_touch_down(event.pos)
            
            elif event.type == pygame.KEYDOWN:
                if self.sleep_manager.is_sleeping:
                    self.sleep_manager.wake_up()
                    self._on_wake()
                    continue
                self.sleep_manager.reset_timer()
                self._handle_key(event.key)
            
            elif event.type == pygame.MOUSEMOTION:
                if self.touch.dragging:
                    self.sleep_manager.reset_timer()
                    self.touch.on_move(event.pos)
                    
                    # Cancel delete mode when user starts swiping
                    if self.touch.is_swiping and self.delete_mode_id:
                        self.delete_mode_id = None
                        self.renderer.invalidate()
            
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
        
        # Check button clicks (add/delete)
        if self._check_button_click(pos):
            return
        
        # Cancel delete mode
        if self.delete_mode_id:
            self.delete_mode_id = None
            self.renderer.invalidate()
        
        # Handle control buttons (just show pressed state, action on release)
        if CONTROLS_Y - PLAY_BTN_SIZE <= y <= CONTROLS_Y + PLAY_BTN_SIZE:
            self._handle_button_down(pos)
            return
        
        # Handle carousel swipes
        if CAROUSEL_Y <= y <= CAROUSEL_Y + COVER_SIZE + 50:
            self.touch.on_down(pos)
            self.user_interacting = True
            self.play_timer.cancel()
    
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
        # Handle button release first (separate from carousel)
        if self._pressed_button:
            self._handle_button_up(pos)
            return
        
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
            # Higher thresholds = less sensitive (need faster swipe for bonus)
            velocity_bonus = 0 if abs_vel < 1.5 else (1 if abs_vel < 2.5 else (2 if abs_vel < 4.0 else 3))
            
            base_target = round(visual_position)
            target = base_target + velocity_bonus if velocity < 0 else base_target - velocity_bonus
            
            # Clamp to valid range
            target = max(0, min(target, len(self.display_items) - 1))
            
            self._snap_to(target)
        elif action == 'tap':
            # Debounce tap actions
            now = time.time()
            if now - self._last_action_time < self._action_debounce:
                logger.debug('Carousel tap debounced')
                return
            
            if x < center_x - 100:
                self._navigate(-1)
            elif x > center_x + 100:
                self._navigate(1)
            elif self.connected or self.mock_mode:
                # Only toggle play if connected
                logger.info('Carousel tap: play')
                logger.debug(f'  connected={self.connected}, playing={self.now_playing.playing}')
                self._last_action_time = now
                self._pressed_button = 'play'
                self._pressed_time = now
                self._play_click()
                self._toggle_play()
                self.renderer.invalidate()
            else:
                logger.info(f'Carousel tap IGNORED (disconnected)')
                logger.info(f'  connected={self.connected}, fail_count={self._connection_fail_count}')
    
    def _handle_button_down(self, pos):
        """Handle button press down - show pressed state only, action on release."""
        x, y = pos
        center_x = SCREEN_WIDTH // 2
        btn_spacing = BTN_SPACING
        
        right_cover_edge = center_x + (COVER_SIZE + COVER_SPACING) + COVER_SIZE_SMALL // 2
        vol_x = right_cover_edge - BTN_SIZE // 2
        
        button = None
        if center_x - btn_spacing - BTN_SIZE <= x <= center_x - btn_spacing + BTN_SIZE:
            button = 'prev'
        elif center_x - PLAY_BTN_SIZE <= x <= center_x + PLAY_BTN_SIZE:
            button = 'play'
        elif center_x + btn_spacing - BTN_SIZE <= x <= center_x + btn_spacing + BTN_SIZE:
            button = 'next'
        elif vol_x - BTN_SIZE <= x <= vol_x + BTN_SIZE:
            button = 'volume'
        
        if button:
            logger.debug(f'Button down: {button}')
            self._pressed_button = button
            self._pressed_time = time.time()
            # Force immediate visual feedback
            self.renderer.invalidate()  # Force full redraw to show pressed state
            self._draw()
            pygame.display.flip()
    
    def _handle_button_up(self, pos):
        """Handle button release - execute action if still on same button."""
        if not self._pressed_button:
            return
        
        pressed = self._pressed_button
        self._pressed_button = None
        
        # Debounce
        now = time.time()
        if now - self._last_action_time < self._action_debounce:
            logger.debug(f'Button release debounced')
            self.renderer.invalidate()
            return
        
        # Don't process API actions if disconnected
        if not self.connected and not self.mock_mode:
            logger.debug(f'Button release ignored (disconnected)')
            self.renderer.invalidate()
            return
        
        x, y = pos
        center_x = SCREEN_WIDTH // 2
        btn_spacing = BTN_SPACING
        right_cover_edge = center_x + (COVER_SIZE + COVER_SPACING) + COVER_SIZE_SMALL // 2
        vol_x = right_cover_edge - BTN_SIZE // 2
        
        # Check which button the release is on
        released_on = None
        if CONTROLS_Y - PLAY_BTN_SIZE <= y <= CONTROLS_Y + PLAY_BTN_SIZE:
            if center_x - btn_spacing - BTN_SIZE <= x <= center_x - btn_spacing + BTN_SIZE:
                released_on = 'prev'
            elif center_x - PLAY_BTN_SIZE <= x <= center_x + PLAY_BTN_SIZE:
                released_on = 'play'
            elif center_x + btn_spacing - BTN_SIZE <= x <= center_x + btn_spacing + BTN_SIZE:
                released_on = 'next'
            elif vol_x - BTN_SIZE <= x <= vol_x + BTN_SIZE:
                released_on = 'volume'
        
        # Execute action only if released on the same button that was pressed
        if released_on == pressed:
            logger.info(f'Button release: {released_on}')
            logger.debug(f'  connected={self.connected}, playing={self.now_playing.playing}')
            self._last_action_time = now
            self._play_click()
            
            if released_on == 'prev':
                threading.Thread(target=self.api.prev, daemon=True).start()
            elif released_on == 'play':
                self._toggle_play()
            elif released_on == 'next':
                threading.Thread(target=self.api.next, daemon=True).start()
            elif released_on == 'volume':
                self._toggle_volume()
        
        self.renderer.invalidate()
    
    def _snap_to(self, target_index: int):
        """Snap carousel to a specific index."""
        items = self.display_items
        if not items:
            return
        
        target_index = max(0, min(target_index, len(items) - 1))
        
        if target_index != self.selected_index:
            old_index = self.selected_index
            self.selected_index = target_index
            self.carousel.set_target(target_index)
            
            # If music is playing and we're navigating away, pause immediately
            if self.now_playing.playing and not self.mock_mode:
                old_item = items[old_index] if old_index < len(items) else None
                if old_item and self._is_item_playing(old_item):
                    logger.info('Pausing for navigation...')
                    self._paused_for_navigation = True
                    self._paused_context_uri = self.now_playing.context_uri
                    # Update UI immediately (optimistic update)
                    self.now_playing = NowPlaying(
                        playing=False, paused=True, stopped=False,
                        context_uri=self.now_playing.context_uri,
                        track_name=self.now_playing.track_name,
                        track_artist=self.now_playing.track_artist,
                        track_album=self.now_playing.track_album,
                        track_cover=self.now_playing.track_cover,
                        position=self.now_playing.position,
                        duration=self.now_playing.duration,
                    )
                    threading.Thread(target=self.api.pause, daemon=True).start()
            
            item = items[target_index]
            logger.debug(f'Snap: {old_index} -> {target_index}, item={item.name}')
            if not item.is_temp:
                # Check if we're returning to the item we paused for navigation
                if (self._paused_for_navigation and 
                    self._paused_context_uri == item.uri):
                    # Resume immediately, no timer needed
                    logger.info(f'Resuming (returned to item): {item.name}')
                    self._paused_for_navigation = False
                    self._paused_context_uri = None
                    self.play_timer.cancel()
                    # Update UI immediately (optimistic update)
                    self.now_playing = NowPlaying(
                        playing=True, paused=False, stopped=False,
                        context_uri=self.now_playing.context_uri,
                        track_name=self.now_playing.track_name,
                        track_artist=self.now_playing.track_artist,
                        track_album=self.now_playing.track_album,
                        track_cover=self.now_playing.track_cover,
                        position=self.now_playing.position,
                        duration=self.now_playing.duration,
                    )
                    threading.Thread(target=self.api.resume, daemon=True).start()
                elif not self._is_item_playing(item):
                    logger.info(f'PlayTimer starting for: {item.name} (index={target_index})')
                    self.play_timer.start(item)
                else:
                    self.play_timer.cancel()
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
        
        # Don't try API calls if disconnected
        if not self.connected:
            logger.debug('Ignoring toggle_play: disconnected')
            return
        
        # Clear navigation pause state on manual toggle
        self._paused_for_navigation = False
        self._paused_context_uri = None
        
        if self.now_playing.playing:
            logger.info('Pausing...')
            self._play_in_progress = False  # Clear any pending play state
            # Update UI immediately (optimistic update)
            self.now_playing = NowPlaying(
                playing=False, paused=True, stopped=False,
                context_uri=self.now_playing.context_uri,
                track_name=self.now_playing.track_name,
                track_artist=self.now_playing.track_artist,
                track_album=self.now_playing.track_album,
                track_cover=self.now_playing.track_cover,
                position=self.now_playing.position,
                duration=self.now_playing.duration,
            )
            threading.Thread(target=self.api.pause, daemon=True).start()
        elif self.now_playing.paused:
            logger.info('Resuming...')
            self.auto_pause.restore_volume_if_needed()
            # Update UI immediately (optimistic update)
            self.now_playing = NowPlaying(
                playing=True, paused=False, stopped=False,
                context_uri=self.now_playing.context_uri,
                track_name=self.now_playing.track_name,
                track_artist=self.now_playing.track_artist,
                track_album=self.now_playing.track_album,
                track_cover=self.now_playing.track_cover,
                position=self.now_playing.position,
                duration=self.now_playing.duration,
            )
            threading.Thread(target=self.api.resume, daemon=True).start()
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
        logger.info(f'Execute play: context_uri={uri[:50]}..., from_beginning={from_beginning}')
        try:
            # Set Spotify volume to 100% on first play
            if not self._spotify_volume_initialized:
                self._spotify_volume_initialized = True
                if self.api.set_volume(100):
                    logger.info('Spotify volume set to 100%')
            
            # Check for saved progress
            skip_to_uri = None
            saved_progress = None
            if not from_beginning:
                saved_progress = self.catalog_manager.get_progress(uri)
                if saved_progress:
                    skip_to_uri = saved_progress.get('uri')
                    logger.info(f'  Saved progress: track={skip_to_uri}, pos={saved_progress.get("position", 0)//1000}s')
                else:
                    logger.info(f'  No saved progress found')
            
            success = self.api.play(uri, skip_to_uri=skip_to_uri)
            logger.info(f'  Play request: success={success}')
            
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
            
            # Execute pending request with debounce delay
            if pending:
                # Wait a bit to allow newer requests to override
                time.sleep(0.5)
                
                # Check again if there's a newer pending request
                with self._play_lock:
                    if self._pending_play:
                        # Newer request came in, use that instead
                        pending = self._pending_play
                        self._pending_play = None
                
                logger.debug(f'Executing queued request: {pending[0]}')
                self._play_item(pending[0], pending[1])
    
    def _init_volume(self):
        """Initialize system volume at startup."""
        level = VOLUME_LEVELS[self.volume_index]['level']
        self._set_system_volume(level)
        self._volume_mode = 'berry'
    
    def _switch_to_berry_mode(self, force: bool = False):
        """Switch to Berry mode: Spotify at 100%, Pi controls volume."""
        if self._volume_mode == 'berry' and not force:
            return
        
        if self._volume_mode != 'berry':
            logger.info('Volume: switching to Berry mode')
        
        self._volume_mode = 'berry'
        
        # Restore Berry's volume choice
        self.volume_index = self._berry_volume_index
        level = VOLUME_LEVELS[self.volume_index]['level']
        self._set_system_volume(level)
        
        # Reset Spotify to 100% (async)
        threading.Thread(target=self._reset_spotify_volume, daemon=True).start()
    
    def _on_wake(self):
        """Called when waking from sleep - reconnect and reset state."""
        logger.info('=' * 40)
        logger.info('WAKE UP START')
        logger.info(f'  Connection state: {self.connected}')
        logger.info(f'  Fail count: {self._connection_fail_count}')
        logger.info(f'  Playing: {self.now_playing.playing}')
        logger.info(f'  Volume mode: {self._volume_mode}')
        
        # Reset connection state for immediate reconnect
        self._connection_fail_count = 0
        self.connected = True  # Optimistic - will correct on next poll if wrong
        self.needs_refresh = True
        
        # Force immediate status refresh in background
        def wake_refresh():
            try:
                self._refresh_status()
                logger.info(f'  Post-refresh connected: {self.connected}')
                logger.info(f'  Post-refresh playing: {self.now_playing.playing}')
                logger.info('WAKE UP COMPLETE')
                logger.info('=' * 40)
            except Exception as e:
                logger.error(f'  Wake refresh failed: {e}')
                logger.info('WAKE UP FAILED')
                logger.info('=' * 40)
        
        threading.Thread(target=wake_refresh, daemon=True).start()
        
        # Reset to Berry mode on wake for clean state
        if self._volume_mode == 'spotify':
            self._switch_to_berry_mode()
    
    def _initial_connect(self):
        """Force initial connection check at startup."""
        try:
            self._refresh_status()
            if self.connected:
                logger.info('Connected to librespot')
        except Exception as e:
            logger.warning(f'Initial connection failed: {e}')
    
    def _reset_spotify_volume(self):
        """Reset Spotify to 100% (called when taking back control)."""
        try:
            self.api.set_volume(100)
        except Exception:
            pass  # Best effort
    
    def _toggle_volume(self):
        """Toggle between volume levels - switches to Berry mode if needed."""
        # Track that we're making a local change
        self._volume_change_time = time.time()
        
        # If in Spotify mode, switch back to Berry mode
        if self._volume_mode == 'spotify':
            logger.info('Volume: taking back control from Spotify')
            self._volume_mode = 'berry'
            threading.Thread(target=self._reset_spotify_volume, daemon=True).start()
        
        # Cycle through volume levels
        self.volume_index = (self.volume_index + 1) % len(VOLUME_LEVELS)
        self._berry_volume_index = self.volume_index  # Remember choice
        level = VOLUME_LEVELS[self.volume_index]['level']
        
        logger.info(f'Volume: {level}%')
        threading.Thread(target=self._set_system_volume, args=(level,), daemon=True).start()
    
    def _set_system_volume(self, level: int):
        """Set the Pi's ALSA system volume."""
        if sys.platform != 'linux':
            return
        try:
            subprocess.run(['amixer', 'set', 'PCM', f'{level}%'],
                          capture_output=True, check=True)
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.debug(f'Could not set system volume: {e}')  # Not critical on non-Pi systems
        except Exception as e:
            logger.warning(f'Unexpected error setting system volume: {e}', exc_info=True)
    
    def _handle_spotify_volume_change(self, spotify_volume: int):
        """Handle volume changes from Spotify (ownership model)."""
        # Skip if we just made a local change
        if time.time() - self._volume_change_time < 2.0:
            return
        
        if self._volume_mode == 'berry':
            # We're in Berry mode - Spotify should be at 100%
            if spotify_volume < 95:
                # Remote user changed volume -> switch to Spotify mode
                logger.info(f'Volume: Spotify took control ({spotify_volume}%)')
                self._volume_mode = 'spotify'
                self._set_system_volume(100)  # Pi to 100%, Spotify controls
                # Update icon to show max (since Pi is at 100%)
                self.volume_index = len(VOLUME_LEVELS) - 1
        else:
            # We're in Spotify mode
            if spotify_volume >= 95:
                # User set Spotify back to ~100% -> switch back to Berry mode
                self._switch_to_berry_mode()
    
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
            # Cleanup temp images after saving (they're now permanent)
            self.catalog_manager.cleanup_temp_images()
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
        """Queue progress save in background thread (non-blocking)."""
        if self.mock_mode:
            return
        
        # Update timestamp immediately to prevent duplicate saves
        self.last_progress_save = time.time()
        
        # Get current context URI before spawning thread
        context_uri = self.now_playing.context_uri
        
        threading.Thread(
            target=self._save_playback_progress_async,
            args=(context_uri,),
            daemon=True
        ).start()
    
    def _save_playback_progress_async(self, fallback_context_uri: Optional[str]):
        """Save current playback position (runs in background thread)."""
        try:
            status = self.api.status()
            if not status or not status.get('track'):
                return
            
            context_uri = status.get('context_uri') or fallback_context_uri
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
            
        except Exception as e:
            logger.warning('Error saving progress', exc_info=True)
    
    def _save_progress_on_shutdown(self):
        """Save progress synchronously before shutdown."""
        if self.mock_mode:
            return
        
        # Check if we have something to save
        if not self.now_playing.playing and not self.now_playing.context_uri:
            logger.debug('No active playback to save on shutdown')
            return
        
        try:
            status = self.api.status()
            if not status or not status.get('track'):
                logger.debug('No track info available for shutdown save')
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
            logger.info(f'Saved progress on shutdown: {track.get("name")} @ {track.get("position", 0) // 1000}s')
            
        except Exception as e:
            logger.warning(f'Could not save progress on shutdown: {e}')
    
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
        # Check for Spotify credentials if in setup mode
        if self.needs_setup:
            now = time.time()
            if now - self._last_credentials_check > 2.0:  # Check every 2 seconds
                self._last_credentials_check = now
                if has_spotify_credentials():
                    logger.info('Spotify credentials detected!')
                    self.needs_setup = False
                    self.renderer.invalidate()
            return  # Skip rest of update while in setup mode
        
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
        
        # Check play timer (only if connected)
        if self.connected or self.mock_mode:
            item_to_play = self.play_timer.check()
            if item_to_play:
                logger.info(f'PlayTimer fired: item={item_to_play.name}, uri={item_to_play.uri[:50]}..., '
                           f'selected_index={self.selected_index}, carousel_pos={self.carousel.scroll_x:.2f}')
                # Check if we should resume (same item that was paused for navigation)
                if (self._paused_for_navigation and 
                    self._paused_context_uri == item_to_play.uri):
                    logger.info(f'  -> Resuming (paused for nav)')
                    self._paused_for_navigation = False
                    self._paused_context_uri = None
                    threading.Thread(target=self.api.resume, daemon=True).start()
                else:
                    # New item, play it
                    logger.info(f'  -> Auto-playing NEW')
                    self._paused_for_navigation = False
                    self._paused_context_uri = None
                    self._play_item(item_to_play.uri)
        else:
            # Cancel play timer if disconnected
            self.play_timer.cancel()
        
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
        
        # Collect playlist covers and update UI if new cover added
        if (self.now_playing.playing and 
            'playlist' in (self.now_playing.context_uri or '')):
            new_cover_added = self.catalog_manager.collect_cover_for_playlist(
                self.now_playing.context_uri,
                self.now_playing.track_cover
            )
            if new_cover_added:
                # Update temp_item with new covers and refresh UI
                self._update_temp_item()
        
        # Check sleep
        self.sleep_manager.check_sleep(self.now_playing.playing)
        
        # Update loading state (calculated here so FPS decision can use it)
        self._update_loading_state()
    
    def _update_loading_state(self):
        """Calculate loading state for spinner display."""
        # Get current selected item
        current_item = self.display_items[self.selected_index] if self.selected_index < len(self.display_items) else None
        current_uri = current_item.uri if current_item else None
        
        # Check if the currently selected item is actually playing
        selected_is_playing = (
            self.now_playing.playing and 
            current_uri and 
            self.now_playing.context_uri == current_uri
        )
        
        # Clear play_in_progress when playback has successfully started
        if selected_is_playing and self._play_in_progress:
            self._play_in_progress = False
        
        # Show loading when:
        # - Paused for navigation (swiped away from playing item)
        # - Play timer running (about to auto-play)
        # - Play request in progress (waiting for playback to start)
        self._should_show_loading = (
            self._paused_for_navigation or 
            self.play_timer.item is not None or 
            self._play_in_progress
        )
        
        # Track loading start time for 200ms delay
        if self._should_show_loading:
            if self._loading_start_time is None:
                self._loading_start_time = time.time()
        else:
            self._loading_start_time = None
        
        # Only show spinner after 200ms delay to avoid flicker
        self._is_loading = (self._should_show_loading and 
                           self._loading_start_time is not None and
                           time.time() - self._loading_start_time > 0.2)
    
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
            pressed_button=self._pressed_button,
            loading=self._is_loading,  # Spinner (with 200ms delay)
            pending_play=self._should_show_loading,  # Play button (immediate)
            needs_setup=self.needs_setup,
        )

