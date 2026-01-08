"""
Librespot API Client - Direct REST API for go-librespot.
"""
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class LibrespotAPI:
    """Direct REST API client for go-librespot."""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers['Content-Type'] = 'application/json'
    
    def status(self) -> Optional[dict]:
        """Get current playback status."""
        try:
            resp = self.session.get(f'{self.base_url}/status', timeout=2)
            if resp.status_code == 204:
                return None
            return resp.json()
        except requests.RequestException as e:
            logger.debug(f'Status request failed: {e}')
            return None
    
    def play(self, uri: str, skip_to_uri: str = None) -> bool:
        """Play a Spotify URI (album/playlist), optionally starting at a specific track."""
        try:
            body = {'uri': uri}
            logger.info(f'API play: context={uri[:50]}...')
            if skip_to_uri:
                body['skip_to_uri'] = skip_to_uri
                logger.info(f'  skip_to_uri: {skip_to_uri}')
            
            resp = self.session.post(
                f'{self.base_url}/player/play',
                json=body,
                timeout=10  # Longer timeout for slow Pi/network
            )
            if resp.ok:
                logger.info('Play request sent')
            else:
                logger.warning(f'Play failed: {resp.status_code} {resp.text}')
            return resp.ok
        except requests.RequestException as e:
            logger.error(f'Play error for URI {uri[:50] if uri else "None"}...: {e}', exc_info=True)
            return False
    
    def pause(self) -> bool:
        """Pause playback."""
        try:
            resp = self.session.post(f'{self.base_url}/player/pause', timeout=2)
            logger.debug(f'Pause: {resp.status_code}')
            return resp.ok
        except requests.RequestException as e:
            logger.error('Pause error', exc_info=True)
            return False
    
    def resume(self) -> bool:
        """Resume playback."""
        try:
            resp = self.session.post(f'{self.base_url}/player/resume', timeout=2)
            logger.debug(f'Resume: {resp.status_code}')
            return resp.ok
        except requests.RequestException as e:
            logger.error('Resume error', exc_info=True)
            return False
    
    def next(self) -> bool:
        """Skip to next track."""
        try:
            resp = self.session.post(f'{self.base_url}/player/next', timeout=2)
            logger.debug(f'Next: {resp.status_code}')
            return resp.ok
        except requests.RequestException as e:
            logger.error('Next error', exc_info=True)
            return False
    
    def prev(self) -> bool:
        """Skip to previous track."""
        try:
            resp = self.session.post(f'{self.base_url}/player/prev', timeout=2)
            logger.debug(f'Prev: {resp.status_code}')
            return resp.ok
        except requests.RequestException as e:
            logger.error('Prev error', exc_info=True)
            return False
    
    def seek(self, position: int) -> bool:
        """Seek to position in milliseconds."""
        try:
            resp = self.session.post(
                f'{self.base_url}/player/seek',
                json={'position': position},
                timeout=2
            )
            logger.debug(f'Seek to {position}ms: {resp.status_code}')
            return resp.ok
        except requests.RequestException as e:
            logger.error(f'Seek error to position {position}ms', exc_info=True)
            return False
    
    def set_volume(self, level: int) -> bool:
        """Set volume level (0-100)."""
        try:
            resp = self.session.post(
                f'{self.base_url}/player/volume',
                json={'volume': level},
                timeout=2
            )
            logger.debug(f'Volume {level}%: {resp.status_code}')
            return resp.ok
        except requests.RequestException as e:
            logger.error(f'Volume error setting level {level}%', exc_info=True)
            return False
    
    def is_connected(self) -> bool:
        """Check if librespot is reachable."""
        try:
            resp = self.session.get(f'{self.base_url}/status', timeout=1)
            return resp.status_code in (200, 204)
        except requests.RequestException:
            return False

