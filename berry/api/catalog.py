"""
Catalog Manager - Unified catalog operations.

Handles:
- Loading/saving catalog items
- Image download and deduplication  
- Playlist cover collection
- Progress tracking for resume
"""
import json
import time
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
from io import BytesIO

import requests
from PIL import Image, ImageDraw

from ..models import CatalogItem
from ..config import PROGRESS_EXPIRY_HOURS, COVER_SIZE

logger = logging.getLogger(__name__)


def apply_rounded_corners_pil(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners to a PIL image with transparency."""
    size = img.size[0]
    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=255)
    result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result


class CatalogManager:
    """
    Unified catalog manager for albums and playlists.
    
    Handles save/delete, image dedup, progress tracking, and playlist covers.
    """
    
    def __init__(self, catalog_path: Path, images_path: Path, mock_mode: bool = False):
        self.catalog_path = catalog_path
        self.images_path = images_path
        self.mock_mode = mock_mode
        
        # Ensure images directory exists
        self.images_path.mkdir(parents=True, exist_ok=True)
        
        # Hash -> local_path for deduplication
        self.image_hashes: Dict[str, str] = {}
        
        # Playlist covers collection: {context_uri: {hash: local_path}}
        self.playlist_covers: Dict[str, Dict[str, str]] = {}
        
        # Track tried URLs to avoid repeated downloads (with max size to prevent memory growth)
        self._tried_cover_urls: set = set()
        self._max_tried_urls = 500
        
        # Cached items
        self._items: List[CatalogItem] = []
        
        # Index existing images on startup
        self._index_existing_images()
    
    # ============================================
    # LOADING & SAVING
    # ============================================
    
    def load(self) -> List[CatalogItem]:
        """Load catalog items from disk."""
        if self.mock_mode:
            self._items = self._load_mock_data()
            return self._items
        
        try:
            logger.info(f'Loading catalog from {self.catalog_path}')
            if self.catalog_path.exists():
                data = json.loads(self.catalog_path.read_text())
                items_data = data.get('items', []) if isinstance(data, dict) else []
                self._items = [
                    CatalogItem(
                        id=item.get('id', ''),
                        uri=item.get('uri', ''),
                        name=item.get('name', ''),
                        type=item.get('type', 'album'),
                        artist=item.get('artist'),
                        image=item.get('image'),
                        images=item.get('images'),
                        current_track=item.get('currentTrack'),
                    )
                    for item in items_data
                    if isinstance(item, dict) and item.get('type') != 'track'
                ]
                logger.info(f'Loaded {len(self._items)} items')
            else:
                logger.warning(f'Catalog not found at {self.catalog_path}')
                self._items = []
        except json.JSONDecodeError as e:
            logger.error(f'Invalid JSON in catalog file: {e}', exc_info=True)
            self._items = []
        except (IOError, OSError) as e:
            logger.error(f'Cannot read catalog file: {e}', exc_info=True)
            self._items = []
        except Exception as e:
            logger.error(f'Unexpected error loading catalog: {e}', exc_info=True)
            self._items = []
        
        return self._items
    
    @property
    def items(self) -> List[CatalogItem]:
        """Get cached catalog items."""
        return self._items
    
    def _load_raw(self) -> dict:
        """Load raw catalog.json."""
        try:
            if self.catalog_path.exists():
                return json.loads(self.catalog_path.read_text())
            return {'items': []}
        except json.JSONDecodeError as e:
            logger.warning(f'Invalid JSON in catalog: {e}')
            return {'items': []}
        except (IOError, OSError) as e:
            logger.error(f'Cannot read catalog file: {e}', exc_info=True)
            return {'items': []}
        except Exception as e:
            logger.error(f'Unexpected error loading catalog: {e}', exc_info=True)
            return {'items': []}
    
    def _save_raw(self, catalog: dict):
        """Save raw catalog.json."""
        self.catalog_path.write_text(json.dumps(catalog, indent=2))
    
    def _load_mock_data(self) -> List[CatalogItem]:
        """Load mock data for UI testing."""
        return [
            CatalogItem(
                id='1', uri='spotify:album:mock1',
                name='Abbey Road', type='album',
                artist='The Beatles',
                image='https://i.scdn.co/image/ab67616d0000b273dc30583ba717007b00cceb25',
                current_track={'name': 'Come Together', 'artist': 'The Beatles'}
            ),
            CatalogItem(
                id='2', uri='spotify:album:mock2',
                name='Dark Side of the Moon', type='album',
                artist='Pink Floyd',
                image='https://i.scdn.co/image/ab67616d0000b273ea7caaff71dea1051d49b2fe',
            ),
            CatalogItem(
                id='3', uri='spotify:album:mock3',
                name='Rumours', type='album',
                artist='Fleetwood Mac',
                image='https://i.scdn.co/image/ab67616d0000b273e52a59a28efa4773dd2bfe1b',
            ),
            CatalogItem(
                id='4', uri='spotify:album:mock4',
                name='Back in Black', type='album',
                artist='AC/DC',
                image='https://i.scdn.co/image/ab67616d0000b2734809adfae9bd679cffadd3a3',
            ),
            CatalogItem(
                id='5', uri='spotify:album:mock5',
                name='Thriller', type='album',
                artist='Michael Jackson',
                image='https://i.scdn.co/image/ab67616d0000b27334bfb69e00898660fc3c3ab3',
            ),
        ]
    
    # ============================================
    # IMAGE HANDLING
    # ============================================
    
    def _index_existing_images(self):
        """Index existing images by extracting hash from filename."""
        try:
            for file in self.images_path.iterdir():
                if file.suffix not in ('.jpg', '.png'):
                    continue
                # Extract hash from filename: "1767089701460-6aa1f146.png" -> "6aa1f146"
                match = file.name.split('-')
                if len(match) >= 2:
                    hash_part = match[-1].replace('.jpg', '').replace('.png', '')
                    if len(hash_part) == 8:  # Valid 8-char hash
                        self.image_hashes[hash_part] = f'/images/{file.name}'
            
            logger.info(f'Indexed {len(self.image_hashes)} images')
        except (IOError, OSError) as e:
            logger.warning(f'Error indexing images: {e}', exc_info=True)
        except Exception as e:
            logger.warning(f'Unexpected error indexing images: {e}', exc_info=True)
    
    def _download_and_hash_image(self, image_url: str) -> tuple:
        """Download image and return (hash, PIL Image)."""
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        buffer = response.content
        hash_full = hashlib.md5(buffer).hexdigest()
        hash_short = hash_full[:8]  # Use first 8 chars like backend
        
        # Load and process with PIL
        img = Image.open(BytesIO(buffer)).convert('RGBA')
        img = img.resize((COVER_SIZE, COVER_SIZE), Image.Resampling.LANCZOS)
        radius = max(12, COVER_SIZE // 25)  # 16px at 410
        img = apply_rounded_corners_pil(img, radius)
        
        return (hash_short, img)
    
    def _save_image(self, hash_short: str, img: Image.Image, temp: bool = False) -> str:
        """Save processed image to disk and return local path."""
        # Check if already exists
        if hash_short in self.image_hashes:
            return self.image_hashes[hash_short]
        
        # Save new image as PNG (preserves transparency)
        prefix = 'temp_' if temp else ''
        filename = f'{prefix}{int(time.time() * 1000)}-{hash_short}.png'
        filepath = self.images_path / filename
        img.save(filepath, 'PNG')
        
        local_path = f'/images/{filename}'
        self.image_hashes[hash_short] = local_path
        logger.info(f'Saved {"temp " if temp else ""}image: {local_path}')
        return local_path
    
    def download_temp_image(self, image_url: str) -> Optional[str]:
        """Download and process image temporarily for temp items.
        
        Returns local path to processed image, or None on error.
        """
        if not image_url or not image_url.startswith('http'):
            return None
        
        try:
            hash_short, img = self._download_and_hash_image(image_url)
            local_path = self._save_image(hash_short, img, temp=True)
            return local_path
        except requests.RequestException as e:
            logger.debug(f'Error downloading temp image: {e}')
            return None
        except Exception as e:
            logger.warning(f'Unexpected error downloading temp image: {e}', exc_info=True)
            return None
    
    def cleanup_temp_images(self) -> int:
        """Remove all temporary images (prefixed with 'temp_'). Returns count deleted."""
        if self.mock_mode:
            return 0
        
        try:
            deleted = 0
            for file in self.images_path.iterdir():
                if file.name.startswith('temp_') and file.suffix == '.png':
                    file.unlink()
                    deleted += 1
                    # Remove from hash index
                    hash_part = file.name.split('-')[-1].replace('.png', '')
                    self.image_hashes = {h: p for h, p in self.image_hashes.items()
                                         if p != f'/images/{file.name}'}
            
            if deleted:
                logger.info(f'Cleanup: {deleted} temp images deleted')
            return deleted
            
        except (IOError, OSError) as e:
            logger.warning(f'Error cleaning up temp images: {e}', exc_info=True)
            return 0
        except Exception as e:
            logger.warning(f'Unexpected error cleaning up temp images: {e}', exc_info=True)
            return 0
    
    # ============================================
    # PLAYLIST COVER COLLECTION
    # ============================================
    
    def collect_cover_for_playlist(self, context_uri: str, cover_url: str) -> bool:
        """Collect album cover URL for playlist composite (max 4 unique).
        
        Stores URLs for later composite creation. Returns True if a new URL was added.
        """
        if 'playlist' not in context_uri or not cover_url:
            return False
        
        if context_uri not in self.playlist_covers:
            self.playlist_covers[context_uri] = {}
        
        covers = self.playlist_covers[context_uri]
        if len(covers) >= 4:
            return False  # Already have 4 covers
        
        # Skip if we've already tried this URL recently
        url_key = f'{context_uri}:{cover_url}'
        if url_key in self._tried_cover_urls:
            return False
        
        # Cleanup if cache is too large (prevent memory growth)
        if len(self._tried_cover_urls) > self._max_tried_urls:
            logger.debug(f'Clearing tried URLs cache ({len(self._tried_cover_urls)} entries)')
            self._tried_cover_urls.clear()
        
        self._tried_cover_urls.add(url_key)
        
        try:
            # Download to get hash for deduplication
            response = requests.get(cover_url, timeout=10)
            response.raise_for_status()
            buffer = response.content
            hash_full = hashlib.md5(buffer).hexdigest()
            hash_short = hash_full[:8]
            
            # Skip if already have this hash for this context
            if hash_short in covers:
                logger.debug(f'Cover already collected (same album): {len(covers)}/4')
                return False
            
            # Store URL and buffer for later composite creation
            covers[hash_short] = {'url': cover_url, 'buffer': buffer}
            logger.info(f'Collected cover {len(covers)}/4 for playlist')
            
            # Create composite if we have enough covers
            if len(covers) >= 4:
                self._update_playlist_covers_if_needed(context_uri)
            
            return True
            
        except requests.RequestException as e:
            logger.debug(f'Error downloading cover image: {e}')
            return False
        except Exception as e:
            logger.warning(f'Error collecting cover: {e}', exc_info=True)
            return False
    
    def _create_composite_from_collected(self, context_uri: str) -> Optional[str]:
        """Create composite image from collected covers and save to disk."""
        if context_uri not in self.playlist_covers:
            return None
        
        covers = self.playlist_covers[context_uri]
        if not covers:
            return None
        
        try:
            half_size = COVER_SIZE // 2  # 205px
            composite = Image.new('RGBA', (COVER_SIZE, COVER_SIZE), (0, 0, 0, 0))
            positions = [(0, 0), (half_size, 0), (0, half_size), (half_size, half_size)]
            
            # Get cover buffers
            cover_buffers = [c['buffer'] for c in covers.values()]
            
            # Pad to 4 by repeating
            while len(cover_buffers) < 4 and cover_buffers:
                cover_buffers.append(cover_buffers[len(cover_buffers) % len(covers)])
            
            for i, (buffer, pos) in enumerate(zip(cover_buffers, positions)):
                try:
                    img = Image.open(BytesIO(buffer)).convert('RGBA')
                    img = img.resize((half_size, half_size), Image.Resampling.LANCZOS)
                    composite.paste(img, pos)
                except Exception as e:
                    logger.debug(f'Error processing cover {i}: {e}')
                    # Draw placeholder
                    draw = ImageDraw.Draw(composite)
                    draw.rectangle([pos, (pos[0] + half_size, pos[1] + half_size)], fill=(40, 40, 40))
            
            # Apply rounded corners
            radius = max(12, COVER_SIZE // 25)
            composite = apply_rounded_corners_pil(composite, radius)
            
            # Generate hash from composite
            composite_bytes = BytesIO()
            composite.save(composite_bytes, 'PNG')
            hash_short = hashlib.md5(composite_bytes.getvalue()).hexdigest()[:8]
            
            # Save composite
            filename = f'{int(time.time() * 1000)}-{hash_short}_composite.png'
            filepath = self.images_path / filename
            composite.save(filepath, 'PNG')
            
            local_path = f'/images/{filename}'
            logger.info(f'Created composite image: {local_path}')
            return local_path
            
        except Exception as e:
            logger.warning(f'Error creating composite: {e}', exc_info=True)
            return None
    
    def _update_playlist_covers_if_needed(self, context_uri: str):
        """Update saved playlist with composite when we have enough covers."""
        covers = self.playlist_covers.get(context_uri, {})
        if len(covers) < 4:
            return  # Wait until we have 4 covers
        
        try:
            catalog = self._load_raw()
            item = next((i for i in catalog['items'] if i['uri'] == context_uri), None)
            
            if not item or item.get('type') != 'playlist':
                return
            
            # Skip if already has a composite image
            current_image = item.get('image', '')
            if '_composite' in current_image:
                return
            
            # Create composite
            composite_path = self._create_composite_from_collected(context_uri)
            if composite_path:
                item['image'] = composite_path
                # Remove old images array if present
                if 'images' in item:
                    del item['images']
                self._save_raw(catalog)
                logger.info(f'Updated playlist with composite image')
                
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.warning(f'Error updating playlist covers: {e}', exc_info=True)
        except Exception as e:
            logger.warning(f'Unexpected error updating playlist covers: {e}', exc_info=True)
    
    def get_collected_covers_count(self, context_uri: str) -> int:
        """Get number of collected covers for a playlist."""
        if context_uri in self.playlist_covers:
            return len(self.playlist_covers[context_uri])
        return 0
    
    # ============================================
    # SAVE & DELETE
    # ============================================
    
    def save_item(self, item_data: dict) -> bool:
        """Save item to catalog with image download and deduplication."""
        if self.mock_mode:
            return True
        
        try:
            catalog = self._load_raw()
            
            # Check for duplicates
            uri = item_data.get('uri')
            if any(i['uri'] == uri for i in catalog['items']):
                logger.warning(f'Item already in catalog: {item_data.get("name")}')
                return False
            
            local_image = None
            image_url = item_data.get('image')
            
            # Check if we already have a temp image (from temp item) - rename to permanent
            if image_url and image_url.startswith('/images/'):
                image_filename = image_url.replace('/images/', '')
                if image_filename.startswith('temp_'):
                    # Rename temp image to permanent
                    old_path = self.images_path / image_filename
                    if old_path.exists():
                        # Extract hash from filename
                        parts = image_filename.replace('temp_', '').replace('.png', '').split('-')
                        if len(parts) >= 2:
                            hash_short = parts[-1]
                            new_filename = f'{int(time.time() * 1000)}-{hash_short}.png'
                            new_path = self.images_path / new_filename
                            old_path.rename(new_path)
                            local_image = f'/images/{new_filename}'
                            self.image_hashes[hash_short] = local_image
                            logger.info(f'Renamed temp image to permanent: {local_image}')
                else:
                    # Already permanent image, reuse it
                    local_image = image_url
            
            # For playlists: create composite from collected covers
            if not local_image and item_data.get('type') == 'playlist' and uri in self.playlist_covers:
                covers = self.playlist_covers[uri]
                if covers:
                    local_image = self._create_composite_from_collected(uri)
                    if local_image:
                        logger.info(f'Created composite from {len(covers)} collected covers')
            
            # Download single image if no composite or temp image (albums or playlists without collected covers)
            if not local_image and image_url and image_url.startswith('http'):
                try:
                    hash_short, img = self._download_and_hash_image(image_url)
                    local_image = self._save_image(hash_short, img)
                except requests.RequestException as e:
                    logger.warning(f'Error downloading image from {image_url[:50]}...: {e}')
                    local_image = image_url  # Fallback to URL
                except Exception as e:
                    logger.warning(f'Unexpected error downloading image: {e}', exc_info=True)
                    local_image = image_url  # Fallback to URL
            
            # Build new item (no images array, just single image)
            new_item = {
                'id': str(int(time.time() * 1000)),
                'type': item_data.get('type', 'album'),
                'uri': uri,
                'name': item_data.get('name'),
                'artist': item_data.get('artist'),
                'album': item_data.get('album'),
                'image': local_image or item_data.get('image'),
                'originalImage': item_data.get('image'),
                'addedAt': datetime.now().isoformat(),
            }
            
            catalog['items'].append(new_item)
            self._save_raw(catalog)
            logger.info(f'Saved to catalog: {new_item["name"]}')
            return True
            
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.error(f'Error saving to catalog: {e}', exc_info=True)
            return False
        except Exception as e:
            logger.error(f'Unexpected error saving to catalog: {e}', exc_info=True)
            return False
    
    def delete_item(self, item_id: str) -> bool:
        """Delete item from catalog."""
        if self.mock_mode:
            return True
        
        try:
            catalog = self._load_raw()
            
            index = next((i for i, item in enumerate(catalog['items']) 
                         if item['id'] == item_id), None)
            if index is None:
                logger.warning(f'Item not found: {item_id}')
                return False
            
            removed = catalog['items'].pop(index)
            self._save_raw(catalog)
            logger.info(f'Deleted from catalog: {removed.get("name")}')
            return True
            
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.error(f'Error deleting from catalog: {e}', exc_info=True)
            return False
        except Exception as e:
            logger.error(f'Unexpected error deleting from catalog: {e}', exc_info=True)
            return False
    
    # ============================================
    # PROGRESS TRACKING
    # ============================================
    
    def save_progress(self, context_uri: str, track_uri: str, 
                      position: int, track_name: str = None, artist: str = None):
        """Save playback progress to catalog item."""
        if self.mock_mode or not context_uri or not track_uri:
            return
        
        try:
            catalog = self._load_raw()
            item = next((i for i in catalog['items'] if i['uri'] == context_uri), None)
            
            if not item:
                logger.debug(f'Context not in catalog (tempItem?): {context_uri[:40]}...')
                return
            
            current_track = {
                'uri': track_uri,
                'position': position,
                'name': track_name,
                'artist': artist,
                'updatedAt': datetime.now().isoformat()
            }
            item['currentTrack'] = current_track
            self._save_raw(catalog)
            
            # Also update in-memory items so UI shows correct track immediately
            for mem_item in self.items:
                if mem_item.uri == context_uri:
                    mem_item.current_track = current_track
                    break
            
            logger.debug(f'Saved progress: {track_name} @ {position // 1000}s')
            
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.warning(f'Error saving progress: {e}', exc_info=True)
        except Exception as e:
            logger.warning(f'Unexpected error saving progress: {e}', exc_info=True)
    
    def get_progress(self, context_uri: str) -> Optional[dict]:
        """Get saved progress if < 24 hours old."""
        if self.mock_mode:
            return None
        
        try:
            catalog = self._load_raw()
            item = next((i for i in catalog['items'] if i['uri'] == context_uri), None)
            
            if not item or 'currentTrack' not in item:
                return None
            
            current_track = item['currentTrack']
            
            # Check age
            updated_at = current_track.get('updatedAt')
            if updated_at:
                updated = datetime.fromisoformat(updated_at)
                age_hours = (datetime.now() - updated).total_seconds() / 3600
                if age_hours > PROGRESS_EXPIRY_HOURS:
                    logger.debug(f'Progress expired ({age_hours:.1f}h old)')
                    self.clear_progress(context_uri)
                    return None
            
            logger.info(f'Resume: "{current_track.get("name")}" @ {current_track.get("position", 0) // 1000}s')
            return current_track
            
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.warning(f'Error getting progress: {e}', exc_info=True)
            return None
        except Exception as e:
            logger.warning(f'Unexpected error getting progress: {e}', exc_info=True)
            return None
    
    def clear_progress(self, context_uri: str):
        """Clear saved progress for a context."""
        if self.mock_mode or not context_uri:
            return
        
        try:
            catalog = self._load_raw()
            item = next((i for i in catalog['items'] if i['uri'] == context_uri), None)
            
            if item and 'currentTrack' in item:
                del item['currentTrack']
                self._save_raw(catalog)
                logger.debug(f'Cleared progress for: {item.get("name")}')
                
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.warning(f'Error clearing progress: {e}', exc_info=True)
        except Exception as e:
            logger.warning(f'Unexpected error clearing progress: {e}', exc_info=True)
    
    # ============================================
    # CLEANUP
    # ============================================
    
    def cleanup_unused_images(self) -> int:
        """Delete images not referenced in catalog. Returns count deleted."""
        if self.mock_mode:
            return 0
        
        try:
            catalog = self._load_raw()
            
            # Collect all used images
            used = set()
            for item in catalog['items']:
                if item.get('image', '').startswith('/images/'):
                    used.add(item['image'].replace('/images/', ''))
            
            # Find and delete unused
            deleted = 0
            for file in self.images_path.iterdir():
                if file.name not in used and file.suffix in ('.jpg', '.png'):
                    file.unlink()
                    deleted += 1
                    # Remove from hash index
                    self.image_hashes = {h: p for h, p in self.image_hashes.items()
                                         if p != f'/images/{file.name}'}
            
            if deleted:
                logger.info(f'Cleanup: {deleted} unused images deleted')
            return deleted
            
        except (IOError, OSError) as e:
            logger.warning(f'Error cleaning up images: {e}', exc_info=True)
            return 0
        except Exception as e:
            logger.warning(f'Unexpected error cleaning up images: {e}', exc_info=True)
            return 0

