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

import requests

from ..models import CatalogItem
from ..config import PROGRESS_EXPIRY_HOURS

logger = logging.getLogger(__name__)


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
        
        # Track tried URLs to avoid repeated downloads
        self._tried_cover_urls: set = set()
        
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
        except Exception as e:
            logger.error(f'Error loading catalog: {e}')
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
        except Exception:
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
                if not file.suffix == '.jpg':
                    continue
                # Extract hash from filename: "1767089701460-6aa1f146.jpg" -> "6aa1f146"
                match = file.name.split('-')
                if len(match) >= 2:
                    hash_part = match[-1].replace('.jpg', '')
                    if len(hash_part) == 8:  # Valid 8-char hash
                        self.image_hashes[hash_part] = f'/images/{file.name}'
            
            logger.info(f'Indexed {len(self.image_hashes)} images')
        except Exception as e:
            logger.warning(f'Error indexing images: {e}')
    
    def _download_and_hash_image(self, image_url: str) -> tuple:
        """Download image and return (hash, buffer)."""
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        buffer = response.content
        hash_full = hashlib.md5(buffer).hexdigest()
        hash_short = hash_full[:8]  # Use first 8 chars like backend
        return (hash_short, buffer)
    
    def _save_image(self, hash_short: str, buffer: bytes) -> str:
        """Save image to disk and return local path."""
        # Check if already exists
        if hash_short in self.image_hashes:
            return self.image_hashes[hash_short]
        
        # Save new image
        filename = f'{int(time.time() * 1000)}-{hash_short}.jpg'
        filepath = self.images_path / filename
        filepath.write_bytes(buffer)
        
        local_path = f'/images/{filename}'
        self.image_hashes[hash_short] = local_path
        logger.info(f'Saved new image: {local_path}')
        return local_path
    
    # ============================================
    # PLAYLIST COVER COLLECTION
    # ============================================
    
    def collect_cover_for_playlist(self, context_uri: str, cover_url: str):
        """Collect album cover for playlist composite (max 4 unique)."""
        if 'playlist' not in context_uri or not cover_url:
            return
        
        if context_uri not in self.playlist_covers:
            self.playlist_covers[context_uri] = {}
        
        covers = self.playlist_covers[context_uri]
        if len(covers) >= 4:
            return  # Already have 4 covers
        
        # Skip if we've already tried this URL recently
        url_key = f'{context_uri}:{cover_url}'
        if url_key in self._tried_cover_urls:
            return
        self._tried_cover_urls.add(url_key)
        
        try:
            hash_short, buffer = self._download_and_hash_image(cover_url)
            
            # Skip if already have this hash for this context
            if hash_short in covers:
                logger.debug(f'Cover already collected (same album): {len(covers)}/4')
                return
            
            # Save/reuse image
            local_path = self._save_image(hash_short, buffer)
            covers[hash_short] = local_path
            logger.info(f'Collected cover {len(covers)}/4 for playlist')
            
            # Update catalog if this playlist is already saved
            self._update_playlist_covers_if_needed(context_uri, local_path)
            
        except Exception as e:
            logger.warning(f'Error collecting cover: {e}')
    
    def _update_playlist_covers_if_needed(self, context_uri: str, local_path: str):
        """Update saved playlist with new covers progressively."""
        try:
            catalog = self._load_raw()
            item = next((i for i in catalog['items'] if i['uri'] == context_uri), None)
            
            if not item or item.get('type') != 'playlist':
                return
            
            current_images = item.get('images') or []
            if len(current_images) >= 4:
                return
            if local_path in current_images:
                return
            
            item['images'] = current_images + [local_path]
            self._save_raw(catalog)
            logger.info(f'Updated saved playlist cover {len(item["images"])}/4')
        except Exception as e:
            logger.warning(f'Error updating playlist covers: {e}')
    
    def get_collected_covers(self, context_uri: str) -> Optional[List[str]]:
        """Get collected covers for a playlist."""
        if context_uri in self.playlist_covers:
            return list(self.playlist_covers[context_uri].values())
        return None
    
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
            local_images = None
            
            # For playlists: use collected covers if available
            if item_data.get('type') == 'playlist' and uri in self.playlist_covers:
                covers = list(self.playlist_covers[uri].values())
                if covers:
                    local_images = covers
                    local_image = covers[0]
                    logger.info(f'Using {len(covers)} pre-collected covers for playlist')
            
            # Download single image if no composite (albums)
            image_url = item_data.get('image')
            if not local_image and image_url and image_url.startswith('http'):
                try:
                    hash_short, buffer = self._download_and_hash_image(image_url)
                    local_image = self._save_image(hash_short, buffer)
                except Exception as e:
                    logger.warning(f'Error downloading image: {e}')
                    local_image = image_url  # Fallback to URL
            
            # Build new item
            new_item = {
                'id': str(int(time.time() * 1000)),
                'type': item_data.get('type', 'album'),
                'uri': uri,
                'name': item_data.get('name'),
                'artist': item_data.get('artist'),
                'album': item_data.get('album'),
                'image': local_image or item_data.get('image'),
                'images': local_images,
                'originalImage': item_data.get('image'),
                'addedAt': datetime.now().isoformat(),
            }
            
            catalog['items'].append(new_item)
            self._save_raw(catalog)
            logger.info(f'Saved to catalog: {new_item["name"]}')
            return True
            
        except Exception as e:
            logger.error(f'Error saving to catalog: {e}')
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
            
        except Exception as e:
            logger.error(f'Error deleting from catalog: {e}')
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
            
            item['currentTrack'] = {
                'uri': track_uri,
                'position': position,
                'name': track_name,
                'artist': artist,
                'updatedAt': datetime.now().isoformat()
            }
            self._save_raw(catalog)
            logger.debug(f'Saved progress: {track_name} @ {position // 1000}s')
            
        except Exception as e:
            logger.warning(f'Error saving progress: {e}')
    
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
            
        except Exception as e:
            logger.warning(f'Error getting progress: {e}')
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
                
        except Exception as e:
            logger.warning(f'Error clearing progress: {e}')
    
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
                for img in item.get('images') or []:
                    if img and img.startswith('/images/'):
                        used.add(img.replace('/images/', ''))
            
            # Find and delete unused
            deleted = 0
            for file in self.images_path.iterdir():
                if file.name not in used and file.suffix == '.jpg':
                    file.unlink()
                    deleted += 1
                    # Remove from hash index
                    self.image_hashes = {h: p for h, p in self.image_hashes.items()
                                         if p != f'/images/{file.name}'}
            
            if deleted:
                logger.info(f'Cleanup: {deleted} unused images deleted')
            return deleted
            
        except Exception as e:
            logger.warning(f'Error cleaning up images: {e}')
            return 0

