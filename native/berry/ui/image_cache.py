"""
Image Cache - Downloads and caches album cover images.
"""
import time
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict
from io import BytesIO

import pygame
import requests
from PIL import Image

from .helpers import apply_rounded_corners, draw_aa_rounded_rect
from ..config import COLORS, COVER_SIZE, COVER_SIZE_SMALL, IMAGE_CACHE_MAX_SIZE

logger = logging.getLogger(__name__)


class ImageCache:
    """Downloads and caches album cover images with pre-loading support."""
    
    def __init__(self, images_dir: Path):
        self.images_dir = images_dir
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.cache: Dict[str, pygame.Surface] = {}
        self.loading: set = set()
        self._preload_queue: List[tuple] = []
        self._preload_lock = threading.Lock()
        self._preloading = False
    
    def get_placeholder(self, size: int) -> pygame.Surface:
        """Get a placeholder surface for missing images."""
        cache_key = f'_placeholder_{size}'
        if cache_key not in self.cache:
            placeholder = pygame.Surface((size, size), pygame.SRCALPHA)
            radius = max(12, size // 25)
            draw_aa_rounded_rect(placeholder, COLORS['bg_elevated'], 
                                (0, 0, size, size), radius)
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
                    # Add composite images for playlists
                    if item.images:
                        for img in item.images:
                            if img:
                                self._preload_queue.append((img, size // 2, False))
        
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
            except Exception:
                pass
            
            # Small delay to not block main thread
            time.sleep(0.01)
    
    def _evict_if_needed(self):
        """Evict old cache entries if cache is too large."""
        if len(self.cache) > IMAGE_CACHE_MAX_SIZE:
            # Remove oldest entries (simple FIFO)
            keys_to_remove = list(self.cache.keys())[:len(self.cache) - IMAGE_CACHE_MAX_SIZE + 20]
            for key in keys_to_remove:
                if not key.startswith('_'):  # Keep placeholders
                    del self.cache[key]
            logger.debug(f'Evicted {len(keys_to_remove)} cached images')
    
    def get(self, image_path: Optional[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get an image surface, loading from disk or URL if needed."""
        if not image_path:
            return self.get_placeholder(size)
        
        cache_key = f'{image_path}_{size}'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Evict old entries if cache is getting too large
        self._evict_if_needed()
        
        # Try to load from local file
        if image_path.startswith('/images/'):
            local_path = self.images_dir / image_path.replace('/images/', '')
            if local_path.exists():
                return self._load_local(local_path, size, cache_key)
        
        # Try to load from URL
        if image_path.startswith('http'):
            if image_path not in self.loading:
                self.loading.add(image_path)
                thread = threading.Thread(
                    target=self._download,
                    args=(image_path, size, cache_key),
                    daemon=True
                )
                thread.start()
        
        return self.get_placeholder(size)
    
    def get_dimmed(self, image_path: Optional[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get a pre-cached dimmed version of the image (for non-selected items)."""
        if not image_path:
            return self.get_placeholder(size)
        
        cache_key = f'{image_path}_{size}_dimmed'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # Get the regular version first
        regular = self.get(image_path, size)
        regular_key = f'{image_path}_{size}'
        
        # Only create dimmed if we have the real image (not placeholder)
        if regular_key in self.cache:
            result = pygame.Surface((size, size), pygame.SRCALPHA)
            result.blit(regular, (0, 0))
            # Apply dark overlay to simulate dimming
            overlay = pygame.Surface((size, size), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 115))  # 45% dark overlay
            result.blit(overlay, (0, 0))
            result = result.convert_alpha()
            self.cache[cache_key] = result
            return result
        
        return regular
    
    def get_composite(self, images: List[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get a 2x2 composite cover for playlists."""
        if not images:
            return self.get_placeholder(size)
        
        images_key = tuple(images[:4]) if images else ()
        cache_key = f'composite_{hash(images_key)}_{size}'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        self._evict_if_needed()
        
        composite = pygame.Surface((size, size), pygame.SRCALPHA)
        half_size = size // 2
        
        positions = [(0, 0), (half_size, 0), (0, half_size), (half_size, half_size)]
        
        for i, pos in enumerate(positions):
            if i < len(images) and images[i]:
                sub_img = self._get_raw(images[i], half_size)
                if sub_img:
                    composite.blit(sub_img, pos)
            else:
                pygame.draw.rect(composite, COLORS['bg_elevated'], 
                               (*pos, half_size, half_size))
        
        radius = max(12, size // 25)
        composite = apply_rounded_corners(composite, radius)
        composite = composite.convert_alpha()
        
        self.cache[cache_key] = composite
        return composite
    
    def get_composite_dimmed(self, images: List[str], size: int = COVER_SIZE) -> pygame.Surface:
        """Get a dimmed composite cover for playlists."""
        if not images:
            return self.get_placeholder(size)
        
        images_key = tuple(images[:4]) if images else ()
        cache_key = f'composite_dimmed_{hash(images_key)}_{size}'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        regular = self.get_composite(images, size)
        regular_key = f'composite_{hash(images_key)}_{size}'
        
        if regular_key in self.cache:
            result = pygame.Surface((size, size), pygame.SRCALPHA)
            result.blit(regular, (0, 0))
            overlay = pygame.Surface((size, size), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 115))
            result.blit(overlay, (0, 0))
            result = result.convert_alpha()
            self.cache[cache_key] = result
            return result
        
        return regular
    
    def _get_raw(self, image_path: str, size: int) -> Optional[pygame.Surface]:
        """Get image without rounded corners (for composite pieces)."""
        cache_key = f'{image_path}_{size}_raw'
        
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        if image_path.startswith('/images/'):
            local_path = self.images_dir / image_path.replace('/images/', '')
            if local_path.exists():
                try:
                    img = Image.open(local_path)
                    img = img.convert('RGBA')
                    img = img.resize((size, size), Image.Resampling.LANCZOS)
                    surface = pygame.image.fromstring(img.tobytes(), img.size, 'RGBA')
                    surface = surface.convert_alpha()
                    self.cache[cache_key] = surface
                    return surface
                except Exception:
                    pass
        
        return None
    
    def _load_local(self, path: Path, size: int, cache_key: str) -> pygame.Surface:
        """Load image from local file."""
        try:
            img = Image.open(path)
            img = img.convert('RGBA')
            img = img.resize((size, size), Image.Resampling.LANCZOS)
            surface = pygame.image.fromstring(img.tobytes(), img.size, 'RGBA')
            radius = max(12, size // 25)
            surface = apply_rounded_corners(surface, radius)
            surface = surface.convert_alpha()
            self.cache[cache_key] = surface
            return surface
        except Exception:
            return self.get_placeholder(size)
    
    def _download(self, url: str, size: int, cache_key: str):
        """Download image from URL in background."""
        try:
            resp = requests.get(url, timeout=10)
            img = Image.open(BytesIO(resp.content))
            img = img.convert('RGBA')
            img = img.resize((size, size), Image.Resampling.LANCZOS)
            surface = pygame.image.fromstring(img.tobytes(), img.size, 'RGBA')
            radius = max(12, size // 25)
            surface = apply_rounded_corners(surface, radius)
            surface = surface.convert_alpha()
            self.cache[cache_key] = surface
        except Exception as e:
            logger.warning(f'Error downloading image: {e}')
        finally:
            self.loading.discard(url)

