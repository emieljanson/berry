"""
Berry Configuration - All constants and settings.
"""
import os
import sys
from pathlib import Path

# ============================================
# SCREEN & DISPLAY
# ============================================

SCREEN_WIDTH = 1280
SCREEN_HEIGHT = 720

# ============================================
# NETWORK ENDPOINTS
# ============================================

LIBRESPOT_URL = os.environ.get('LIBRESPOT_URL', 'http://localhost:3678')
LIBRESPOT_WS = os.environ.get('LIBRESPOT_WS', 'ws://localhost:3678/events')
BACKEND_URL = os.environ.get('BACKEND_URL', 'http://localhost:3001')

# ============================================
# PATHS
# ============================================

# Use backend's data folder (shared catalog & images)
DATA_DIR = Path(__file__).parent.parent.parent / 'backend' / 'data'
CATALOG_PATH = DATA_DIR / 'catalog.json'
IMAGES_DIR = DATA_DIR / 'images'
ICONS_DIR = Path(__file__).parent.parent / 'icons'

# ============================================
# COMMAND LINE FLAGS
# ============================================

MOCK_MODE = '--mock' in sys.argv or '-m' in sys.argv
FULLSCREEN = '--fullscreen' in sys.argv or '-f' in sys.argv

# ============================================
# COLORS (Design specs from web version)
# ============================================

COLORS = {
    'bg_primary': (13, 13, 13),
    'bg_secondary': (26, 26, 26),
    'bg_elevated': (40, 40, 40),
    'accent': (189, 101, 252),  # Purple #BD65FC
    'accent_hover': (205, 130, 255),
    'text_primary': (255, 255, 255),
    'text_secondary': (160, 160, 160),
    'text_muted': (96, 96, 96),
    'success': (29, 185, 84),
    'error': (232, 80, 80),
}

# ============================================
# LAYOUT & SIZES
# ============================================

# Cover sizes
COVER_SIZE = 410
COVER_SIZE_SMALL = int(COVER_SIZE * 0.75)  # ~307
COVER_SPACING = 20

# Layout positions
TRACK_INFO_Y = 45    # Track name near top
CAROUSEL_Y = 120     # Below track info
CONTROLS_Y = 620     # Bottom area

# Button sizes
BTN_SIZE = 100
PLAY_BTN_SIZE = 120

# Progress bar
PROGRESS_BAR_HEIGHT = 8

# ============================================
# VOLUME
# ============================================

VOLUME_LEVELS = [
    {'level': 50, 'icon': 'volume_none'},
    {'level': 75, 'icon': 'volume_low'},
    {'level': 100, 'icon': 'volume_high'},
]

# ============================================
# TIMING
# ============================================

SLEEP_TIMEOUT = 120.0  # 2 minutes of inactivity
PLAY_TIMER_DELAY = 1.0  # seconds before auto-play
SYNC_COOLDOWN = 3.0  # Block sync for 3s after play timer fires
PROGRESS_SAVE_INTERVAL = 10  # Save progress every 10 seconds
PROGRESS_EXPIRY_HOURS = 24  # Expire saved progress after 24 hours

# ============================================
# TOUCH & GESTURES
# ============================================

SWIPE_THRESHOLD = 50      # Minimum distance for swipe
SWIPE_VELOCITY = 0.3      # Minimum velocity (pixels/ms)
LONG_PRESS_TIME = 1.0     # Time for long press (seconds)

# ============================================
# PERFORMANCE
# ============================================

PERF_LOG_INTERVAL = 5.0   # Log performance every 5 seconds
PERF_SAMPLE_SIZE = 60     # Average over 60 frames
IMAGE_CACHE_MAX_SIZE = 200  # Maximum cached images

