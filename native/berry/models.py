"""
Berry Data Models - Core data structures.
"""
from dataclasses import dataclass
from typing import Optional, List


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

