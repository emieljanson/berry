"""
Tests for PlaybackController - play/pause, navigation pause, progress.
"""
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from berry.controllers.playback import PlaybackController
from berry.models import CatalogItem, NowPlaying


def _make_controller(**overrides):
    """Create a PlaybackController with mocked dependencies."""
    api = MagicMock()
    api.status.return_value = None
    api.play.return_value = True
    api.pause.return_value = True
    api.resume.return_value = True
    api.seek.return_value = True
    api.set_volume.return_value = True
    api.is_connected.return_value = True

    catalog = MagicMock()
    catalog.get_progress.return_value = None
    catalog.save_progress = MagicMock()
    catalog.clear_progress = MagicMock()

    volume = MagicMock()
    volume.ensure_spotify_at_100 = MagicMock()

    defaults = dict(
        api=api,
        catalog_manager=catalog,
        volume=volume,
        mock_mode=False,
    )
    defaults.update(overrides)
    pc = PlaybackController(**defaults)
    return pc, api, catalog, volume


def _make_item(uri='spotify:album:test1', name='Test Album') -> CatalogItem:
    return CatalogItem(id='1', uri=uri, name=name, type='album')


class TestTogglePlay:
    """Tests for play/pause toggling."""

    def test_pause_when_playing(self):
        pc, api, _, _ = _make_controller()
        np = NowPlaying(playing=True, context_uri='spotify:album:x')
        items = [_make_item(uri='spotify:album:x')]
        pc.toggle_play(items, 0, np)
        assert pc.play_state.pending_action == 'pause'

    def test_resume_when_paused(self):
        pc, api, _, _ = _make_controller()
        on_resume = MagicMock()
        pc._on_resume = on_resume
        np = NowPlaying(paused=True, context_uri='spotify:album:x')
        items = [_make_item(uri='spotify:album:x')]
        pc.toggle_play(items, 0, np)
        assert pc.play_state.pending_action == 'play'
        on_resume.assert_called_once()

    def test_play_when_stopped(self):
        pc, api, _, _ = _make_controller()
        np = NowPlaying(stopped=True)
        item = _make_item(uri='spotify:album:new')
        pc.toggle_play([item], 0, np)
        assert pc.last_user_play_uri == 'spotify:album:new'


class TestNavigationPause:
    """Tests for pause-on-swipe-away / resume-on-return."""

    def test_pause_for_navigation(self):
        pc, api, _, _ = _make_controller()
        pc.pause_for_navigation('spotify:album:x')
        assert pc.paused_for_navigation is True
        assert pc.paused_context_uri == 'spotify:album:x'

    def test_resume_from_navigation(self):
        pc, api, _, _ = _make_controller()
        pc.pause_for_navigation('spotify:album:x')
        pc.resume_from_navigation()
        assert pc.paused_for_navigation is False
        assert pc.paused_context_uri is None

    def test_clear_navigation_pause(self):
        pc, api, _, _ = _make_controller()
        pc.pause_for_navigation('spotify:album:x')
        pc.clear_navigation_pause()
        assert pc.paused_for_navigation is False

    def test_pause_for_navigation_is_idempotent(self):
        """Second call should be a no-op (no duplicate API pause)."""
        pc, api, _, _ = _make_controller()
        pc.pause_for_navigation('spotify:album:x')
        pc.pause_for_navigation('spotify:album:x')
        # api.pause is called via run_async, but the guard prevents the second call
        assert pc.paused_for_navigation is True

    def test_cancel_pending_clears_queued_play(self):
        pc, api, _, _ = _make_controller()
        pc._pending_play = ('spotify:album:queued', False)
        pc.cancel_pending()
        assert pc._pending_play is None


class TestAutoplay:
    """Tests for autoplay detection."""

    def test_detects_autoplay(self):
        pc, _, catalog, _ = _make_controller()
        pc.last_context_uri = 'spotify:album:old'
        pc.last_user_play_time = 0  # Long ago
        np = NowPlaying(playing=True, context_uri='spotify:album:new')
        pc.check_autoplay(np)
        catalog.clear_progress.assert_called_once_with('spotify:album:old')

    def test_ignores_recent_user_action(self):
        pc, _, catalog, _ = _make_controller()
        pc.last_context_uri = 'spotify:album:old'
        pc.last_user_play_time = time.time()  # Just now
        np = NowPlaying(playing=True, context_uri='spotify:album:new')
        pc.check_autoplay(np)
        catalog.clear_progress.assert_not_called()


class TestProgressSave:
    """Tests for periodic progress saving."""

    def test_save_progress_respects_interval(self):
        pc, api, catalog, _ = _make_controller()
        pc.last_progress_save = time.time()  # Just saved
        np = NowPlaying(playing=True, context_uri='spotify:album:x')
        pc.save_progress(np)
        # Should not have submitted a save (too recent)
        api.status.assert_not_called()

    def test_skip_when_not_playing(self):
        pc, api, _, _ = _make_controller()
        pc.last_progress_save = 0
        np = NowPlaying(playing=False)
        pc.save_progress(np)
        api.status.assert_not_called()


class TestIsItemPlaying:
    """Tests for is_item_playing check."""

    def test_returns_true_when_matching(self):
        pc, _, _, _ = _make_controller()
        item = _make_item(uri='spotify:album:x')
        np = NowPlaying(playing=True, context_uri='spotify:album:x')
        assert pc.is_item_playing(item, np) is True

    def test_returns_false_when_different(self):
        pc, _, _, _ = _make_controller()
        item = _make_item(uri='spotify:album:x')
        np = NowPlaying(playing=True, context_uri='spotify:album:y')
        assert pc.is_item_playing(item, np) is False


class TestLoadingState:
    """Tests for loading/spinner state updates."""

    def test_loading_starts_when_play_in_progress(self):
        pc, _, _, _ = _make_controller()
        pc._play_in_progress = True
        np = NowPlaying()
        pc.update_loading_state(np, carousel_settled=True, play_timer_active=False)
        # Loading is tracked but is_loading has a 200ms delay
        assert pc.play_state.loading_since is not None

    def test_loading_visible_after_delay(self):
        pc, _, _, _ = _make_controller()
        pc._play_in_progress = True
        np = NowPlaying()
        pc.update_loading_state(np, carousel_settled=True, play_timer_active=False)
        pc.play_state.loading_since = time.time() - 1  # Fake elapsed time
        assert pc.play_state.is_loading is True

    def test_loading_continues_while_play_in_progress(self):
        """Loading stays active while _execute_play is running, even if now_playing shows playing."""
        pc, _, _, _ = _make_controller()
        pc._play_in_progress = True
        np = NowPlaying(playing=True)
        pc.update_loading_state(np, carousel_settled=True, play_timer_active=False)
        assert pc.play_state.loading_since is not None

    def test_loading_stops_when_play_completes(self):
        pc, _, _, _ = _make_controller()
        pc._play_in_progress = False
        np = NowPlaying(playing=True)
        pc.update_loading_state(np, carousel_settled=True, play_timer_active=False)
        assert pc.play_state.loading_since is None

    def test_loading_during_navigation_pause(self):
        pc, _, _, _ = _make_controller()
        pc.pause_for_navigation('spotify:album:x')
        np = NowPlaying()
        pc.update_loading_state(np, carousel_settled=False, play_timer_active=False)
        assert pc.play_state.loading_since is not None


class TestPlayFailure:
    """Tests for play failure recovery (e.g. no active Spotify session)."""

    def test_play_failure_clears_pending_state(self):
        pc, api, _, _ = _make_controller()
        api.play.return_value = None
        toast = MagicMock()
        pc._on_toast = toast

        pc._execute_play('spotify:album:x', from_beginning=False)

        assert pc.play_state.pending_action is None
        assert pc.play_state.loading_since is None
        toast.assert_called_once_with('Verbind via Spotify')

    def test_transient_failure_no_toast(self):
        pc, api, _, _ = _make_controller()
        api.play.return_value = False
        toast = MagicMock()
        pc._on_toast = toast

        pc._execute_play('spotify:album:x', from_beginning=False)

        assert pc.play_state.pending_action is None
        assert pc.play_state.loading_since is None
        toast.assert_not_called()

    def test_play_success_keeps_pending_state(self):
        pc, api, _, _ = _make_controller()
        api.play.return_value = True

        pc.play_state.set_pending('play')
        pc._execute_play('spotify:album:x', from_beginning=False)

        assert pc.play_state.pending_action == 'play'
