"""
Performance Monitor - FPS and frame time tracking.
"""
import time
import logging
from typing import List

from ..config import PERF_LOG_INTERVAL, PERF_SAMPLE_SIZE

logger = logging.getLogger(__name__)


class PerformanceMonitor:
    """Tracks FPS and frame times for performance debugging."""
    
    def __init__(self):
        self.frame_times: List[float] = []
        self.last_log_time = time.time()
        self.is_animating = False
        self._last_animation_state = False
    
    def update(self, dt: float, is_animating: bool):
        """Update with frame delta time. Call every frame."""
        self.frame_times.append(dt)
        if len(self.frame_times) > PERF_SAMPLE_SIZE:
            self.frame_times.pop(0)
        
        self.is_animating = is_animating
        
        # Log when animation starts/stops, or periodically during animation
        now = time.time()
        
        # Log on animation state change
        if is_animating and not self._last_animation_state:
            self._log_stats("Animation started")
        elif not is_animating and self._last_animation_state:
            self._log_stats("Animation ended")
        # Log periodically during animation
        elif is_animating and now - self.last_log_time >= PERF_LOG_INTERVAL:
            self._log_stats("Animating")
        
        self._last_animation_state = is_animating
    
    def _log_stats(self, prefix: str):
        """Log current performance stats to console."""
        if not self.frame_times:
            return
        
        avg_dt = sum(self.frame_times) / len(self.frame_times)
        fps = 1.0 / avg_dt if avg_dt > 0 else 0
        frame_ms = avg_dt * 1000
        
        logger.debug(f'{prefix} | FPS: {fps:.1f} | Frame: {frame_ms:.1f}ms')
        self.last_log_time = time.time()
    
    @property
    def current_fps(self) -> float:
        """Get current average FPS."""
        if not self.frame_times:
            return 0
        avg_dt = sum(self.frame_times) / len(self.frame_times)
        return 1.0 / avg_dt if avg_dt > 0 else 0
    
    @property
    def current_frame_ms(self) -> float:
        """Get current average frame time in ms."""
        if not self.frame_times:
            return 0
        return (sum(self.frame_times) / len(self.frame_times)) * 1000

