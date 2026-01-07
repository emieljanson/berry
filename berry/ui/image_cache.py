"""
Image Cache - Loads and caches album cover images.

Images are stored on disk in 4 pre-scaled variants:
- {hash}.png           - 410px normal
- {hash}_small.png     - 307px normal
- {hash}_dim.png       - 410px dimmed
- {hash}_small_dim.png - 307px dimmed

This eliminates runtime PIL resizing/alpha compositing for much better FPS.
"""
import time
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict

import pygame

from ..config import COLORS, COVER_SIZE, COVER_SIZE_SMALL, IMAGE_CACHE_MAX_SIZE

logger = logging.getLogger(__name__)


class ImageCache:
    """Loads and caches pre-scaled album cover images.
    
    All image variants are pre-generated at download time by catalog.py.
    This cache just loads the right variant with pygame (fast!) and maintains
    a surface cache for even faster subsequent access.
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
    
    def _get_variant_path(self, image_path: str, size: int, dimmed: bool = False) -> Path:
        """Get the path to the correct image variant.
        
        Handles both new format (hash.png) and old format (timestamp-hash.png).
        """
        if not image_path.startswith('/images/'):
            return None
        
        filename = image_path.replace('/images/', '')
        base = filename.replace('.png', '').replace('.jpg', '')
        
        # Determine suffix based on size and dimmed
        if size == COVER_SIZE_SMALL:
            suffix = '_small_dim' if dimmed else '_small'
        else:
            suffix = '_dim' if dimmed else ''
        
        # Try new format first: {hash}{suffix}.png
        variant_filename = f'{base}{suffix}.png'
        variant_path = self.images_dir / variant_filename
        
        if variant_path.exists():
            return variant_path
        
        # Fall back to original file for old format images (will be slower)
        # This handles images not yet migrated
        original_path = self.images_dir / filename
        if original_path.exists():
            logger.debug(f'Variant {variant_filename} not found, using original: {filename}')
            return original_path
        
        logger.warning(f'Image not found: {variant_filename} (base={base}, size={size}, dimmed={dimmed})')
        return None
    
    def _load_surface(self, path: Path, cache_key: str) -> pygame.Surface:
        """Load image directly with pygame (fast - no PIL resize needed)."""
        try:
            surface = pygame.image.load(str(path)).convert_alpha()
            self.cache[cache_key] = surface
            self._access_times[cache_key] = time.time()
            return surface
        except Exception as e:
            logger.debug(f'Failed to load {path}: {e}')
            # Extract size from cache_key for placeholder
            # cache_key format: "{path}_{size}" or "{path}_{size}_dimmed"
            try:
                parts = cache_key.rsplit('_', 2)
                if 'dimmed' in parts[-1]:
                    size = int(parts[-2])
                else:
                    size = int(parts[-1])
            except (ValueError, IndexError):
                size = COVER_SIZE
            return self.get_placeholder(size)
    
    def get(self, image_path: Optional[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get an image surface, loading the correct pre-scaled variant.
        
        No PIL resize needed - variants are pre-generated at download time.
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
        
        # Get path to correct variant
        variant_path = self._get_variant_path(image_path, size, dimmed=False)
        
        if variant_path:
            return self._load_surface(variant_path, cache_key)
        
        # URL images or missing files
        return self.get_placeholder(size)
    
    def get_dimmed(self, image_path: Optional[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get a pre-dimmed image variant (for non-selected items).
        
        No PIL alpha composite needed - dimmed variants are pre-generated.
        """
        if not image_path:
            return self.get_placeholder(size)
        
        cache_key = f'{image_path}_{size}_dimmed'
        
        if cache_key in self.cache:
            self._access_times[cache_key] = time.time()
            return self.cache[cache_key]
        
        # Evict old entries if cache is getting too large
        self._evict_if_needed()
        
        # Get path to dimmed variant
        variant_path = self._get_variant_path(image_path, size, dimmed=True)
        
        if variant_path:
            return self._load_surface(variant_path, cache_key)
        
        return self.get_placeholder(size)
