"""
Playback Controller - Manages play/pause/resume, progress, and navigation pause.

Extracted from Berry.app to keep playback logic isolated and testable.
"""
import time
import logging
import threading
from typing import Optional, Callable, List

from ..api.librespot import LibrespotAPIProtocol
from ..api.catalog import CatalogManager
from ..models import CatalogItem, NowPlaying, PlayState
from ..config import PROGRESS_SAVE_INTERVAL
from ..utils import run_async
from .volume import VolumeController

logger = logging.getLogger(__name__)


class PlaybackController:
    """Owns play/pause/resume, progress tracking, and navigation pause."""

    def __init__(
        self,
        api: LibrespotAPIProtocol,
        catalog_manager: CatalogManager,
        volume: VolumeController,
        mock_mode: bool = False,
        on_toast: Optional[Callable[[str], None]] = None,
        on_invalidate: Optional[Callable[[], None]] = None,
        on_resume: Optional[Callable[[], None]] = None,
    ):
        self.api = api
        self.catalog_manager = catalog_manager
        self.volume = volume
        self.mock_mode = mock_mode
        self._on_toast = on_toast or (lambda msg: None)
        self._on_invalidate = on_invalidate or (lambda: None)
        self._on_resume = on_resume or (lambda: None)

        # Play request queuing (non-blocking, latest wins)
        self._play_lock = threading.Lock()
        self._play_in_progress = False
        self._pending_play: Optional[tuple] = None

        # Navigation pause (pause when swiping away from playing item)
        self._paused_for_navigation = False
        self._paused_context_uri: Optional[str] = None

        # UI loading/spinner state
        self.play_state = PlayState()

        # Track user-initiated plays (for autoplay detection)
        self.last_user_play_time: float = 0
        self.last_user_play_uri: Optional[str] = None

        # Progress tracking
        self.last_context_uri: Optional[str] = None
        self.last_progress_save: float = 0
        self.last_saved_track_uri: Optional[str] = None

        # Mock playback
        self.mock_playing = False
        self.mock_position = 0
        self.mock_duration = 180000

    @property
    def paused_for_navigation(self) -> bool:
        return self._paused_for_navigation

    @property
    def paused_context_uri(self) -> Optional[str]:
        return self._paused_context_uri

    def is_item_playing(self, item: CatalogItem, now_playing: NowPlaying) -> bool:
        """Check if an item is currently playing."""
        return item.uri == now_playing.context_uri

    def toggle_play(self, items: List[CatalogItem], selected_index: int, now_playing: NowPlaying):
        """Toggle play/pause based on current state."""
        if not items:
            return

        self._paused_for_navigation = False
        self._paused_context_uri = None

        if now_playing.playing:
            logger.info('Pausing...')
            self.play_state.set_pending('pause')
            self._play_in_progress = False
            self._on_invalidate()
            run_async(self.api.pause)
        elif now_playing.paused:
            logger.info('Resuming...')
            self.play_state.set_pending('play')
            self._on_invalidate()
            self._on_resume()
            run_async(self.api.resume)
        else:
            item = items[selected_index]
            logger.info(f'Playing {item.name}')
            self.play_item(item.uri)

    def play_item(self, uri: str, from_beginning: bool = False):
        """Queue a play request (non-blocking). Only the latest request runs."""
        self.last_user_play_time = time.time()
        self.last_user_play_uri = uri

        with self._play_lock:
            if self._play_in_progress:
                self._pending_play = (uri, from_beginning)
                logger.debug(f'Queued play request: {uri}')
                return
            self._play_in_progress = True

        run_async(self._execute_play, uri, from_beginning)

    def cancel_pending(self):
        """Cancel any pending play requests (e.g. when user starts new swipe)."""
        with self._play_lock:
            self._pending_play = None

    def pause_for_navigation(self, context_uri: str):
        """Pause playback when user swipes away from playing item.
        
        Idempotent: only sends one pause API call per navigation sequence.
        """
        if self._paused_for_navigation:
            return
        logger.info('Pausing for navigation...')
        self._paused_for_navigation = True
        self._paused_context_uri = context_uri
        run_async(self.api.pause)

    def resume_from_navigation(self):
        """Resume playback when user returns to the paused item."""
        logger.info('Resuming (returned to item)')
        self._paused_for_navigation = False
        self._paused_context_uri = None
        run_async(self.api.resume)

    def clear_navigation_pause(self):
        """Clear the navigation pause state without resuming."""
        self._paused_for_navigation = False
        self._paused_context_uri = None

    def check_autoplay(self, now_playing: NowPlaying):
        """Detect autoplay and clear progress when context finishes naturally."""
        new_context = now_playing.context_uri
        old_context = self.last_context_uri

        if (old_context and new_context and
                old_context != new_context and
                now_playing.playing):
            recent_user_action = time.time() - self.last_user_play_time < 5
            expected_context = new_context == self.last_user_play_uri
            if not recent_user_action and not expected_context:
                logger.info(f'Context finished: {old_context}')
                self.catalog_manager.clear_progress(old_context)

    def save_progress(self, now_playing: NowPlaying, force: bool = False):
        """Queue a periodic progress save if due (or immediately if force=True)."""
        if self.mock_mode:
            return
        if not now_playing.playing and not force:
            return
        if not force and time.time() - self.last_progress_save <= PROGRESS_SAVE_INTERVAL:
            return
        self.last_progress_save = time.time()
        context_uri = now_playing.context_uri
        run_async(self._save_progress_async, context_uri)

    def save_progress_on_shutdown(self, now_playing: NowPlaying):
        """Save progress synchronously before shutdown."""
        if self.mock_mode:
            return
        if not now_playing.playing and not now_playing.context_uri:
            logger.debug('No active playback to save on shutdown')
            return
        try:
            status = self.api.status()
            if not status or not status.get('track'):
                logger.debug('No track info available for shutdown save')
                return
            context_uri = status.get('context_uri') or now_playing.context_uri
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

    def update_loading_state(self, now_playing: NowPlaying, carousel_settled: bool,
                             play_timer_active: bool):
        """Update the loading/spinner state each frame."""
        if self._paused_for_navigation:
            if carousel_settled and not play_timer_active and not self._play_in_progress:
                self._paused_for_navigation = False
                self._paused_context_uri = None

        if self.play_state.pending_action == 'pause':
            self.play_state.stop_loading()
            return

        should_load = (
            self._paused_for_navigation or
            play_timer_active or
            self._play_in_progress
        )
        if should_load:
            self.play_state.start_loading()
        else:
            self.play_state.stop_loading()

    def update_mock(self, dt: float, now_playing: NowPlaying):
        """Advance mock playback position."""
        if not self.mock_mode or not self.mock_playing:
            return
        self.mock_position += int(dt * 1000)
        if self.mock_position >= self.mock_duration:
            self.mock_position = 0
        now_playing.position = self.mock_position

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _execute_play(self, uri: str, from_beginning: bool):
        """Execute the play request (runs in thread pool)."""
        logger.info(f'Execute play: context_uri={uri[:50]}..., from_beginning={from_beginning}')
        try:
            self.volume.ensure_spotify_at_100()

            skip_to_uri = None
            saved_progress = None
            if not from_beginning:
                saved_progress = self.catalog_manager.get_progress(uri)
                if saved_progress:
                    skip_to_uri = saved_progress.get('uri')
                    logger.info(f'  Saved progress: track={skip_to_uri}, pos={saved_progress.get("position", 0) // 1000}s')
                else:
                    logger.info('  No saved progress found')

            need_seek = saved_progress and saved_progress.get('position', 0) > 0

            # Retry a few times — librespot may still be authenticating after restart.
            # play() returns True (ok), None (no session), or False (transient failure).
            result = False
            max_attempts = 4
            retry_delay = 3  # seconds between attempts
            for attempt in range(1, max_attempts + 1):
                result = self.api.play(uri, skip_to_uri=skip_to_uri, paused=need_seek)
                logger.info(f'  Play request attempt {attempt}/{max_attempts}: result={result}')
                if result is True:
                    break
                if result is None:
                    # librespot explicitly says no active session — no point retrying
                    logger.warning('  No active Spotify session, stopping retries')
                    break
                if attempt < max_attempts:
                    self.play_state.start_loading()
                    time.sleep(retry_delay)

            success = result is True
            if not success:
                self.play_state.clear()
                if result is None:
                    # Only show the toast when librespot confirms there's no session
                    self._on_toast('Verbind via Spotify')

            if success and need_seek:
                position = saved_progress['position']
                if self.api.seek(position):
                    logger.info(f'Seeked to {position // 1000}s')
                self.api.resume()
                logger.info('  Resumed after seek')
        finally:
            with self._play_lock:
                self._play_in_progress = False
                pending = self._pending_play
                self._pending_play = None

            if pending:
                time.sleep(0.5)
                with self._play_lock:
                    if self._pending_play:
                        pending = self._pending_play
                        self._pending_play = None
                logger.debug(f'Executing queued request: {pending[0]}')
                self.play_item(pending[0], pending[1])

    def _save_progress_async(self, fallback_context_uri: Optional[str]):
        """Save current playback position (runs in thread pool).
        
        Uses fallback_context_uri (from WebSocket) as the source of truth for
        which context we're saving to. The HTTP status may briefly lag behind
        after a context switch — if the track_uri from HTTP doesn't belong to
        the expected context, we skip the save to avoid writing stale positions.
        """
        try:
            status = self.api.status()
            if not status or not status.get('track'):
                return
            context_uri = fallback_context_uri or status.get('context_uri')
            if not context_uri:
                return
            track = status['track']
            track_uri = track.get('uri')

            # Guard: if HTTP reports a different context than expected, the
            # position data likely belongs to the old context — skip this save.
            http_context = status.get('context_uri')
            if http_context and fallback_context_uri and http_context != fallback_context_uri:
                logger.debug(f'Context mismatch, skipping save (ws={fallback_context_uri[:40]}, http={http_context[:40]})')
                return

            self.catalog_manager.save_progress(
                context_uri,
                track_uri,
                track.get('position', 0),
                track.get('name'),
                ', '.join(track.get('artist_names', []))
            )
            self.last_saved_track_uri = track_uri
        except Exception as e:
            logger.warning('Error saving progress', exc_info=True)
