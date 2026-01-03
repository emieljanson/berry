"""
Event Listener - WebSocket connection to go-librespot.
"""
import json
import time
import logging
import threading
from typing import Callable, Optional

import websocket

logger = logging.getLogger(__name__)


class EventListener:
    """Listens to go-librespot WebSocket events."""
    
    def __init__(self, url: str, on_update: Callable[[], None]):
        """
        Initialize event listener.
        
        Args:
            url: WebSocket URL (e.g., ws://localhost:3678/events)
            on_update: Callback when playback state changes
        """
        self.url = url
        self.on_update = on_update
        self.ws: Optional[websocket.WebSocketApp] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        self.context_uri: Optional[str] = None
    
    def start(self):
        """Start listening for events in background thread."""
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info(f'Started WebSocket listener: {self.url}')
    
    def stop(self):
        """Stop listening for events."""
        self.running = False
        if self.ws:
            self.ws.close()
        logger.info('Stopped WebSocket listener')
    
    def _run(self):
        """Main loop - connects and reconnects as needed."""
        while self.running:
            try:
                self.ws = websocket.WebSocketApp(
                    self.url,
                    on_message=self._on_message,
                    on_error=self._on_error,
                    on_close=self._on_close,
                )
                self.ws.run_forever()
            except Exception as e:
                logger.warning(f'WebSocket error: {e}')
            
            if self.running:
                # Wait before reconnecting
                time.sleep(3)
    
    def _on_message(self, ws, message: str):
        """Handle incoming WebSocket message."""
        try:
            data = json.loads(message)
            event_type = data.get('type')
            
            if event_type == 'playing':
                self.context_uri = data.get('data', {}).get('context_uri')
                logger.debug(f'Playing event, context: {self.context_uri}')
            
            # Notify app to refresh state
            self.on_update()
        except Exception as e:
            logger.warning(f'Error parsing event: {e}')
    
    def _on_error(self, ws, error):
        """Handle WebSocket error."""
        # Suppress errors - we'll reconnect anyway
        pass
    
    def _on_close(self, ws, close_status, close_msg):
        """Handle WebSocket close."""
        pass

