"""
Berry Data Models - Core data structures.
"""
import time
from dataclasses import dataclass, field
from typing import Optional, List, Literal


@dataclass
class CatalogItem:
    """Represents an album or playlist in the catalog."""
    id: str
    uri: str
    name: str
    type: str = 'album'
    artist: Optional[str] = None
    image: Optional[str] = None
    images: Optional[List[str]] = None  # For playlist composite covers
    current_track: Optional[dict] = None
    is_temp: bool = False


@dataclass
class NowPlaying:
    """Current playback state from librespot."""
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
    
    @property
    def is_active(self) -> bool:
        """Check if there's active playback (playing or paused)."""
        return not self.stopped
    
    @property
    def progress(self) -> float:
        """Get playback progress as 0.0-1.0."""
        if self.duration <= 0:
            return 0.0
        return min(1.0, self.position / self.duration)


@dataclass
class PlayState:
    """
    Unified play/loading state for UI feedback.
    
    Replaces multiple separate variables:
    - _optimistic_playing
    - _is_loading / _should_show_loading  
    - _loading_start_time
    """
    pending_action: Optional[Literal['play', 'pause']] = None
    loading_since: Optional[float] = None
    
    # Delay before showing spinner (prevents flicker)
    SPINNER_DELAY = 0.2
    
    def set_pending(self, action: Literal['play', 'pause']):
        """Set a pending play/pause action."""
        self.pending_action = action
        if action == 'play':
            self.loading_since = time.time()
        else:
            self.loading_since = None
    
    def clear(self):
        """Clear pending state (real data received)."""
        self.pending_action = None
        self.loading_since = None
    
    def start_loading(self):
        """Start loading state (for navigation pause, play timer, etc.)."""
        if self.loading_since is None:
            self.loading_since = time.time()
    
    def stop_loading(self):
        """Stop loading state."""
        self.loading_since = None
    
    @property
    def is_loading(self) -> bool:
        """True if loading long enough to show spinner (200ms delay)."""
        if self.loading_since is None:
            return False
        return time.time() - self.loading_since > self.SPINNER_DELAY
    
    @property
    def should_show_loading(self) -> bool:
        """True if in any loading state (for play button icon)."""
        return self.loading_since is not None
    
    def display_playing(self, actual_playing: bool) -> bool:
        """What the UI should show for play/pause state."""
        if self.pending_action == 'pause':
            return False
        if self.pending_action == 'play' or self.should_show_loading:
            return True
        return actual_playing

