"""
Performance Monitor - FPS and frame time tracking with detailed profiling.
"""
import time
import logging
from typing import List, Dict
from collections import defaultdict

from ..config import PERF_LOG_INTERVAL, PERF_SAMPLE_SIZE

logger = logging.getLogger(__name__)


class FrameProfiler:
    """Profile individual frame sections to find bottlenecks."""
    
    def __init__(self):
        self._timings: Dict[str, List[float]] = defaultdict(list)
        self._current_frame: Dict[str, float] = {}
        self._frame_start = 0
        self._enabled = False
        self._last_report = 0
        self._report_interval = 5.0  # Report every 5 seconds when enabled
        self._sample_size = 60  # Keep 60 samples
    
    def enable(self):
        """Enable detailed profiling."""
        self._enabled = True
        logger.info('Frame profiler ENABLED')
    
    def disable(self):
        """Disable detailed profiling."""
        self._enabled = False
        logger.info('Frame profiler DISABLED')
    
    @property
    def enabled(self) -> bool:
        return self._enabled
    
    def start_frame(self):
        """Call at the start of each frame."""
        if not self._enabled:
            return
        self._frame_start = time.perf_counter()
        self._current_frame.clear()
    
    def mark(self, section: str):
        """Mark a section timing (time since frame start)."""
        if not self._enabled:
            return
        elapsed = (time.perf_counter() - self._frame_start) * 1000
        self._current_frame[section] = elapsed
    
    def end_frame(self):
        """Call at the end of each frame."""
        if not self._enabled:
            return
        
        total = (time.perf_counter() - self._frame_start) * 1000
        self._current_frame['total'] = total
        
        # Store timings
        for section, ms in self._current_frame.items():
            self._timings[section].append(ms)
            if len(self._timings[section]) > self._sample_size:
                self._timings[section].pop(0)
        
        # Periodic report
        now = time.time()
        if now - self._last_report >= self._report_interval:
            self._report()
            self._last_report = now
    
    def _report(self):
        """Log average timings for each section."""
        if not self._timings:
            return
        
        sections = []
        for section, times in sorted(self._timings.items()):
            if times:
                avg = sum(times) / len(times)
                sections.append(f'{section}={avg:.1f}ms')
        
        if sections:
            logger.info(f'[PROFILER] {" | ".join(sections)}')


class PerformanceMonitor:
    """Tracks FPS and frame times for performance debugging."""
    
    def __init__(self):
        self.frame_times: List[float] = []
        self.last_log_time = time.time()
        self.is_animating = False
        self._last_animation_state = False
        
        # Detailed profiler (disabled by default)
        self.profiler = FrameProfiler()
    
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

