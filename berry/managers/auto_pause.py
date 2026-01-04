"""
Auto-pause Manager - Pauses playback after extended listening.

Prevents music from playing indefinitely when a child forgets to stop it.
After 30 minutes of continuous play in the same context, fades out and pauses.
"""
import time
import logging
import threading
import subprocess
import sys
from typing import Optional, Callable

from ..config import AUTO_PAUSE_TIMEOUT, AUTO_PAUSE_FADE_DURATION

logger = logging.getLogger(__name__)


class AutoPauseManager:
    """Manages automatic pause after extended playback."""
    
    def __init__(self, on_pause: Callable[[], None], get_volume: Callable[[], int]):
        """
        Args:
            on_pause: Callback to pause playback
            get_volume: Callback to get current volume level (0-100)
        """
        self._on_pause = on_pause
        self._get_volume = get_volume
        
        self._context_uri: Optional[str] = None
        self._play_start_time: Optional[float] = None
        self._is_fading = False
        self._fade_thread: Optional[threading.Thread] = None
        self._original_volume: int = 100
        self._should_restore_volume = False
    
    def on_play(self, context_uri: Optional[str]):
        """Called when playback starts or context changes."""
        if not context_uri:
            self._reset()
            return
        
        # If context changed, reset timer
        if context_uri != self._context_uri:
            logger.info(f'Auto-pause: new context, timer reset ({AUTO_PAUSE_TIMEOUT // 60}min)')
            self._context_uri = context_uri
            self._play_start_time = time.time()
            self._is_fading = False
    
    def on_stop(self):
        """Called when playback stops or pauses."""
        self._reset()
    
    def check(self, is_playing: bool) -> bool:
        """
        Check if auto-pause should trigger.
        Call this periodically (e.g., every second).
        
        Returns True if auto-pause was triggered.
        """
        if not is_playing or not self._play_start_time:
            return False
        
        if self._is_fading:
            return False  # Already fading
        
        elapsed = time.time() - self._play_start_time
        
        if elapsed >= AUTO_PAUSE_TIMEOUT:
            logger.info(f'Auto-pause: {AUTO_PAUSE_TIMEOUT // 60} minutes reached, fading out...')
            self._trigger_fade_out()
            return True
        
        return False
    
    def get_remaining_time(self) -> Optional[float]:
        """Get remaining time until auto-pause (in seconds), or None if not active."""
        if not self._play_start_time:
            return None
        elapsed = time.time() - self._play_start_time
        remaining = AUTO_PAUSE_TIMEOUT - elapsed
        return max(0, remaining)
    
    def restore_volume_if_needed(self):
        """Restore volume after auto-pause (call when user resumes)."""
        if self._should_restore_volume:
            logger.info(f'Auto-pause: restoring volume to {self._original_volume}%')
            self._set_system_volume(self._original_volume)
            self._should_restore_volume = False
    
    def _reset(self):
        """Reset timer state."""
        self._context_uri = None
        self._play_start_time = None
        self._is_fading = False
    
    def _trigger_fade_out(self):
        """Start fade-out in background thread."""
        self._is_fading = True
        self._original_volume = self._get_volume()
        
        self._fade_thread = threading.Thread(target=self._fade_out_and_pause, daemon=True)
        self._fade_thread.start()
    
    def _fade_out_and_pause(self):
        """Fade out volume over FADE_DURATION seconds, then pause."""
        steps = 20  # Number of volume steps
        step_duration = AUTO_PAUSE_FADE_DURATION / steps
        
        for i in range(steps):
            progress = (i + 1) / steps
            new_volume = int(self._original_volume * (1 - progress))
            new_volume = max(0, new_volume)
            
            self._set_system_volume(new_volume)
            time.sleep(step_duration)
        
        # Pause playback
        logger.info('Auto-pause: pausing playback')
        self._on_pause()
        
        # Restore volume (so next play is at normal level)
        time.sleep(0.5)
        self._set_system_volume(self._original_volume)
        self._should_restore_volume = False  # Already restored
        
        # Reset state
        self._reset()
        logger.info('Auto-pause: complete, volume restored')
    
    def _set_system_volume(self, level: int):
        """Set the Pi's ALSA system volume."""
        if sys.platform != 'linux':
            return
        try:
            subprocess.run(
                ['amixer', 'set', 'PCM', f'{level}%'],
                capture_output=True, 
                check=True
            )
        except Exception as e:
            logger.warning(f'Could not set system volume: {e}')

