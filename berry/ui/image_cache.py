"""
Image Cache - Loads and caches album cover images.

Images are stored on disk with rounded corners already applied.
This cache just loads, resizes, and caches pygame surfaces.
"""
import time
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict

import pygame
from PIL import Image

from ..config import COLORS, COVER_SIZE, COVER_SIZE_SMALL, IMAGE_CACHE_MAX_SIZE

logger = logging.getLogger(__name__)


class ImageCache:
    """Loads and caches pre-processed album cover images.
    
    Images are stored on disk with rounded corners already applied by catalog.py.
    This cache just loads, resizes, and maintains a pygame surface cache.
    """
    
    def __init__(self, images_dir: Path):
        self.images_dir = images_dir
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.cache: Dict[str, pygame.Surface] = {}
        self._access_times: Dict[str, float] = {}  # Track last access for LRU eviction
        self._preload_queue: List[tuple] = []
        self._preload_lock = threading.Lock()
        self._preloading = False
    
    def get_placeholder(self, size: int) -> pygame.Surface:
        """Get a placeholder surface for missing images."""
        cache_key = f'_placeholder_{size}'
        if cache_key not in self.cache:
            placeholder = pygame.Surface((size, size), pygame.SRCALPHA)
            radius = max(12, size // 25)
            # Use native pygame.draw.rect with border_radius for cleaner corners
            pygame.draw.rect(placeholder, COLORS['bg_elevated'], 
                            (0, 0, size, size), border_radius=radius)
            self.cache[cache_key] = placeholder.convert_alpha()
        return self.cache[cache_key]
    
    def preload_catalog(self, items: List, sizes: List[int] = None):
        """Pre-load all catalog images in background thread for smooth scrolling."""
        if sizes is None:
            sizes = [COVER_SIZE, COVER_SIZE_SMALL]
        
        with self._preload_lock:
            self._preload_queue.clear()
            for item in items:
                for size in sizes:
                    if item.image:
                        self._preload_queue.append((item.image, size, False))  # Normal
                        self._preload_queue.append((item.image, size, True))   # Dimmed
        
        # Start preload thread if not running
        if not self._preloading:
            self._preloading = True
            thread = threading.Thread(target=self._preload_worker, daemon=True)
            thread.start()
            logger.info(f'Pre-loading {len(self._preload_queue)} images...')
    
    def _preload_worker(self):
        """Background worker to preload images."""
        loaded = 0
        while True:
            with self._preload_lock:
                if not self._preload_queue:
                    self._preloading = False
                    logger.info(f'Pre-loaded {loaded} images')
                    return
                image_path, size, dimmed = self._preload_queue.pop(0)
            
            try:
                if dimmed:
                    self.get_dimmed(image_path, size)
                else:
                    self.get(image_path, size)
                loaded += 1
            except Exception as e:
                logger.debug(f'Failed to pre-load image {image_path}: {e}')
            
            # Small delay to not block main thread
            time.sleep(0.01)
    
    def _evict_if_needed(self):
        """Evict least recently used cache entries if cache is too large."""
        if len(self.cache) > IMAGE_CACHE_MAX_SIZE:
            # Sort by access time (oldest first), excluding placeholders
            evictable = [
                (key, self._access_times.get(key, 0))
                for key in self.cache.keys()
                if not key.startswith('_')  # Keep placeholders
            ]
            evictable.sort(key=lambda x: x[1])  # Sort by access time
            
            # Remove the 20 least recently used entries
            keys_to_remove = [key for key, _ in evictable[:20]]
            for key in keys_to_remove:
                del self.cache[key]
                self._access_times.pop(key, None)
            
            logger.debug(f'Evicted {len(keys_to_remove)} LRU cached images')
    
    def get(self, image_path: Optional[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get an image surface, loading from disk if needed.
        
        Images are stored with rounded corners pre-applied, so we just
        load and resize (corners scale proportionally).
        """
        if not image_path:
            return self.get_placeholder(size)
        
        cache_key = f'{image_path}_{size}'
        
        if cache_key in self.cache:
            # Update access time for LRU tracking
            self._access_times[cache_key] = time.time()
            return self.cache[cache_key]
        
        # Evict old entries if cache is getting too large
        self._evict_if_needed()
        
        # Load from local file (images are pre-processed with corners)
        if image_path.startswith('/images/'):
            local_path = self.images_dir / image_path.replace('/images/', '')
            if local_path.exists():
                return self._load_local(local_path, size, cache_key)
        
        # URL images are handled by catalog during download
        # Show placeholder if not yet available
        return self.get_placeholder(size)
    
    def get_dimmed(self, image_path: Optional[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get a pre-cached dimmed version of the image (for non-selected items).
        
        Uses PIL alpha_composite to properly preserve transparent corners.
        """
        if not image_path:
            return self.get_placeholder(size)
        
        cache_key = f'{image_path}_{size}_dimmed'
        
        if cache_key in self.cache:
            self._access_times[cache_key] = time.time()
            return self.cache[cache_key]
        
        # Load image as PIL for proper alpha compositing
        img = self._load_pil(image_path, size)
        if img is None:
            return self.get_placeholder(size)
        
        # Apply dimming with alpha composite (preserves transparent corners)
        overlay = Image.new('RGBA', (size, size), (0, 0, 0, 115))
        img = Image.alpha_composite(img, overlay)
        
        # Convert to pygame surface
        surface = pygame.image.fromstring(img.tobytes(), img.size, 'RGBA')
        surface = surface.convert_alpha()
        self.cache[cache_key] = surface
        self._access_times[cache_key] = time.time()
        return surface
    
    def _load_pil(self, image_path: str, size: int) -> Optional[Image.Image]:
        """Load image as PIL Image for processing (e.g., dimming)."""
        if not image_path or not image_path.startswith('/images/'):
            return None
        
        local_path = self.images_dir / image_path.replace('/images/', '')
        if not local_path.exists():
            return None
        
        try:
            img = Image.open(local_path).convert('RGBA')
            if img.size[0] != size:
                img = img.resize((size, size), Image.Resampling.LANCZOS)
            return img
        except Exception:
            return None
    
    def _load_local(self, path: Path, size: int, cache_key: str) -> pygame.Surface:
        """Load pre-processed image from local file.
        
        Images already have rounded corners applied, so we just resize if needed.
        """
        try:
            img = Image.open(path).convert('RGBA')
            if img.size[0] != size:
                img = img.resize((size, size), Image.Resampling.LANCZOS)
            surface = pygame.image.fromstring(img.tobytes(), img.size, 'RGBA')
            surface = surface.convert_alpha()
            self.cache[cache_key] = surface
            self._access_times[cache_key] = time.time()
            return surface
        except Exception:
            return self.get_placeholder(size)

