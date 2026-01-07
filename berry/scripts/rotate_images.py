#!/usr/bin/env python3
"""
Rotate existing images 90° CW for portrait display mode.

Run this once after updating to portrait mode to rotate all existing images.
After this, new images will be saved pre-rotated by catalog.py.

Usage:
    python -m berry.scripts.rotate_images
    
Or from project root:
    python berry/scripts/rotate_images.py
"""
import sys
from pathlib import Path

# Add parent to path for imports when run directly
if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PIL import Image


def rotate_images():
    """Rotate all images in data/images/ by 90° CW."""
    images_dir = Path(__file__).parent.parent.parent / 'data' / 'images'
    
    if not images_dir.exists():
        print(f"Images directory not found: {images_dir}")
        return
    
    png_files = list(images_dir.glob('*.png'))
    print(f"Found {len(png_files)} images to rotate")
    
    rotated = 0
    skipped = 0
    
    for path in png_files:
        try:
            img = Image.open(path)
            w, h = img.size
            
            # Skip if already rotated (height > width for portrait)
            # Original images are square, rotated images are still square
            # So we can't detect by dimensions alone
            # Just rotate everything
            
            # Rotate 90° CW
            rotated_img = img.transpose(Image.Transpose.ROTATE_270)
            rotated_img.save(path, 'PNG')
            rotated += 1
            print(f"  ✓ {path.name}")
            
        except Exception as e:
            print(f"  ✗ {path.name}: {e}")
            skipped += 1
    
    print(f"\nDone! Rotated {rotated} images, skipped {skipped}")


if __name__ == '__main__':
    rotate_images()

