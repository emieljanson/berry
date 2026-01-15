"""
Volume Controller - Manages volume state and ownership model.

Ownership model:
- 'berry': Spotify at 100%, Pi controls volume via ALSA
- 'spotify': Pi at 100%, Spotify controls volume (remote user took over)
"""
import time
import logging
from typing import Literal

from ..config import VOLUME_LEVELS
from ..utils import run_async, set_system_volume

logger = logging.getLogger(__name__)


class VolumeController:
    """Manages volume state and ownership between Berry and Spotify."""
    
    def __init__(self, api):
        """
        Args:
            api: LibrespotAPI instance for setting Spotify volume
        """
        self.api = api
        
        # Volume state
        self.mode: Literal['berry', 'spotify'] = 'berry'
        self.index = 1  # Current volume level index
        self._berry_index = 1  # Remember Berry's choice when in Spotify mode
        self._last_change_time = 0  # For ignoring Spotify feedback after local change
        self._spotify_initialized = False  # Set Spotify to 100% on first play
    
    @property
    def speaker_level(self) -> int:
        """Current speaker volume level (0-100)."""
        return VOLUME_LEVELS[self.index]['speaker']
    
    @property
    def headphone_level(self) -> int:
        """Current headphone volume level (0-100)."""
        return VOLUME_LEVELS[self.index]['headphone']
    
    @property
    def icon(self) -> str:
        """Current volume icon name."""
        return VOLUME_LEVELS[self.index]['icon']
    
    def init(self):
        """Initialize system volume at startup."""
        set_system_volume(self.speaker_level, self.headphone_level)
        self.mode = 'berry'
    
    def toggle(self):
        """Cycle through volume levels. Switches to Berry mode if needed."""
        self._last_change_time = time.time()
        
        # If in Spotify mode, take back control
        if self.mode == 'spotify':
            logger.info('Volume: taking back control from Spotify')
            self.mode = 'berry'
            run_async(self._reset_spotify_volume)
        
        # Cycle through volume levels
        self.index = (self.index + 1) % len(VOLUME_LEVELS)
        self._berry_index = self.index
        
        logger.info(f'Volume: speaker={self.speaker_level}%, headphone={self.headphone_level}%')
        run_async(set_system_volume, self.speaker_level, self.headphone_level)
    
    def handle_spotify_change(self, spotify_volume: int):
        """Handle volume changes from Spotify (ownership model)."""
        # Skip if we just made a local change
        if time.time() - self._last_change_time < 2.0:
            return
        
        if self.mode == 'berry':
            # Spotify should be at 100%, if not -> remote user changed it
            if spotify_volume < 95:
                logger.info(f'Volume: Spotify took control ({spotify_volume}%)')
                self.mode = 'spotify'
                set_system_volume(100, 100)  # Pi to 100%, Spotify controls
                self.index = len(VOLUME_LEVELS) - 1  # Show max icon
        else:
            # In Spotify mode, if volume back to ~100% -> switch back to Berry
            if spotify_volume >= 95:
                self._switch_to_berry_mode()
    
    def ensure_spotify_at_100(self) -> bool:
        """Ensure Spotify volume is at 100% (call on first play). Returns True if set."""
        if not self._spotify_initialized:
            self._spotify_initialized = True
            if self.api.set_volume(100):
                logger.info('Spotify volume set to 100%')
                return True
        return False
    
    def on_wake(self):
        """Reset to Berry mode on wake for clean state."""
        if self.mode == 'spotify':
            self._switch_to_berry_mode()
    
    def _switch_to_berry_mode(self, force: bool = False):
        """Switch to Berry mode: Spotify at 100%, Pi controls volume."""
        if self.mode == 'berry' and not force:
            return
        
        if self.mode != 'berry':
            logger.info('Volume: switching to Berry mode')
        
        self.mode = 'berry'
        self.index = self._berry_index
        set_system_volume(self.speaker_level, self.headphone_level)
        run_async(self._reset_spotify_volume)
    
    def _reset_spotify_volume(self):
        """Reset Spotify to 100% (called when taking back control)."""
        try:
            self.api.set_volume(100)
        except Exception:
            pass  # Best effort

