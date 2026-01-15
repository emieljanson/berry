"""
Berry Utilities - Shared helper functions.
"""
import sys
import subprocess
import threading
import logging

logger = logging.getLogger(__name__)


def run_async(fn, *args):
    """Fire-and-forget async execution in daemon thread."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def set_system_volume(level: int):
    """Set the Pi's ALSA system volume (0-100)."""
    if sys.platform != 'linux':
        return
    try:
        subprocess.run(
            ['amixer', 'set', 'PCM', f'{level}%'],
            capture_output=True,
            check=True
        )
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        logger.debug(f'Could not set system volume: {e}')
    except Exception as e:
        logger.warning(f'Unexpected error setting system volume: {e}', exc_info=True)

