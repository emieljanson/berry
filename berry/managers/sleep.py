"""
Sleep Manager - Power saving and screen burn-in prevention.
"""
import os
import time
import logging
from typing import Optional

from ..config import SLEEP_TIMEOUT

logger = logging.getLogger(__name__)


class SleepManager:
    """Manages deep sleep mode for power saving and screen burn-in prevention."""
    
    BACKLIGHT_DIR = '/sys/class/backlight'
    
    def __init__(self):
        self.is_sleeping = False
        self.last_activity = time.time()
        self.backlight_path = self._detect_backlight()
        
        if self.backlight_path:
            logger.info(f'Backlight detected: {self.backlight_path}')
            # Ensure backlight is ON at startup (in case previous session crashed while sleeping)
            self._set_backlight(True)
        else:
            logger.info('No backlight found (not on Pi?)')
    
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
        if time.time() - self.last_activity >= SLEEP_TIMEOUT:
            self.enter_sleep()
            return True
        
        return False
    
    def enter_sleep(self):
        """Enter deep sleep mode - turn off backlight."""
        if self.is_sleeping:
            return
        
        logger.info('Entering sleep mode...')
        self.is_sleeping = True
        self._set_backlight(False)
    
    def wake_up(self):
        """Wake from sleep mode - turn on backlight."""
        if not self.is_sleeping:
            return
        
        logger.info('Waking up...')
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
            logger.debug(f'Backlight {"on" if on else "off"}')
        except Exception as e:
            # Not running on Pi or no permission
            logger.warning(f'Could not control backlight: {e}')

