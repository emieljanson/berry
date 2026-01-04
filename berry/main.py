#!/usr/bin/env python3
"""
Berry Native - Pygame UI for Raspberry Pi

Usage:
    python -m berry              # Windowed (development)
    python -m berry --fullscreen # Fullscreen (Pi)
    python -m berry --mock       # Mock mode (UI testing)
"""
import sys
import logging

from .config import (
    SCREEN_WIDTH, SCREEN_HEIGHT, 
    LIBRESPOT_URL, MOCK_MODE, FULLSCREEN
)
from .app import Berry


def setup_logging():
    """Configure logging for the application."""
    # Determine log level from environment or default to INFO
    import os
    level_name = os.environ.get('BERRY_LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    
    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(level)
    
    # Configure root logger
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(console)
    
    # Quiet down noisy libraries
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('requests').setLevel(logging.WARNING)
    logging.getLogger('websocket').setLevel(logging.WARNING)


def main():
    """Entry point for Berry application."""
    setup_logging()
    
    logger = logging.getLogger(__name__)
    
    logger.info('Berry Native')
    if MOCK_MODE:
        logger.info('Mode: MOCK (UI testing)')
    else:
        logger.info(f'Librespot: {LIBRESPOT_URL}')
    logger.info(f'Screen: {SCREEN_WIDTH}x{SCREEN_HEIGHT}')
    logger.info(f'Fullscreen: {FULLSCREEN}')
    
    print()
    print('Controls:')
    print('   ← →     Navigate carousel')
    print('   Space   Play/Pause')
    print('   N       Next track')
    print('   P       Previous track')
    print('   Esc     Quit')
    print()
    
    app = Berry(fullscreen=FULLSCREEN)
    app.start()


if __name__ == '__main__':
    main()

