#!/usr/bin/env python3
"""
Migrate existing images to the new 4-variant format.

This script processes all existing images in data/images/ and generates
the missing variants (_small, _dim, _small_dim) for each.

Run this once after updating to the new image system:
    python -m berry.scripts.migrate_images

The script is idempotent - running it multiple times is safe.
"""
import sys
import logging
from pathlib import Path

from PIL import Image, ImageDraw

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Image sizes (must match config.py)
COVER_SIZE = 410
COVER_SIZE_SMALL = int(COVER_SIZE * 0.75)  # 307


def apply_rounded_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners to a PIL image with transparency."""
    size = img.size[0]
    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (size - 1, size - 1)], radius=radius, fill=255)
    result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    result.paste(img, (0, 0), mask)
    return result


def apply_dimming(img: Image.Image, alpha: int = 115) -> Image.Image:
    """Apply dark overlay to image (45% dimming by default)."""
    overlay = Image.new('RGBA', img.size, (0, 0, 0, alpha))
    return Image.alpha_composite(img, overlay)


def get_base_name(filename: str) -> str:
    """Extract base name from image filename.
    
    Handles:
    - Old format: 1767089701460-6aa1f146.png -> 6aa1f146
    - New format: 6aa1f146.png -> 6aa1f146
    - Composite: 6aa1f146_composite.png -> 6aa1f146_composite
    """
    stem = Path(filename).stem
    
    # Skip if already a variant
    for suffix in ['_small_dim', '_small', '_dim']:
        if stem.endswith(suffix):
            return None  # This is a variant, skip
    
    # Handle old timestamp-hash format
    if '-' in stem and not stem.startswith('temp_'):
        parts = stem.split('-')
        if len(parts) >= 2 and len(parts[-1]) == 8:
            # Old format, extract hash part
            return parts[-1] if '_composite' not in stem else f'{parts[-1]}_composite'
    
    # Skip temp files (they'll be cleaned up anyway)
    if stem.startswith('temp_'):
        return None
    
    return stem


def migrate_image(images_dir: Path, filename: str) -> int:
    """Generate missing variants for an image. Returns count of files created."""
    base_name = get_base_name(filename)
    if base_name is None:
        return 0  # Skip variants and temp files
    
    source_path = images_dir / filename
    if not source_path.exists():
        return 0
    
    # Check what variants already exist
    variants_needed = []
    for size, suffix in [(COVER_SIZE, ''), (COVER_SIZE_SMALL, '_small')]:
        normal_path = images_dir / f'{base_name}{suffix}.png'
        dim_path = images_dir / f'{base_name}{suffix}_dim.png'
        
        if not normal_path.exists():
            variants_needed.append((size, suffix, False))
        if not dim_path.exists():
            variants_needed.append((size, suffix, True))
    
    if not variants_needed:
        return 0  # All variants exist
    
    # Load source image
    try:
        img = Image.open(source_path).convert('RGBA')
    except Exception as e:
        logger.warning(f'Failed to load {filename}: {e}')
        return 0
    
    created = 0
    for size, suffix, dimmed in variants_needed:
        try:
            # Resize
            resized = img.resize((size, size), Image.Resampling.LANCZOS)
            
            # Apply rounded corners
            radius = max(12, size // 25)
            processed = apply_rounded_corners(resized, radius)
            
            # Apply dimming if needed
            if dimmed:
                processed = apply_dimming(processed)
            
            # Save
            dim_suffix = '_dim' if dimmed else ''
            out_path = images_dir / f'{base_name}{suffix}{dim_suffix}.png'
            processed.save(out_path, 'PNG')
            created += 1
            
        except Exception as e:
            logger.warning(f'Failed to create variant {base_name}{suffix}{"_dim" if dimmed else ""}: {e}')
    
    return created


def cleanup_old_format(images_dir: Path) -> int:
    """Remove old format files after variants are created. Returns count deleted."""
    deleted = 0
    
    for file in images_dir.iterdir():
        if file.suffix != '.png':
            continue
        
        stem = file.stem
        
        # Skip variants, temp files, and new format files
        for suffix in ['_small_dim', '_small', '_dim']:
            if stem.endswith(suffix):
                break
        else:
            # Check if this is old format (timestamp-hash)
            if '-' in stem and not stem.startswith('temp_'):
                parts = stem.split('-')
                if len(parts) >= 2:
                    try:
                        int(parts[0])  # First part is timestamp
                        # Check if new format exists
                        new_name = parts[-1]
                        if '_composite' in stem:
                            new_name = f'{new_name}_composite'
                        
                        if (images_dir / f'{new_name}.png').exists():
                            file.unlink()
                            deleted += 1
                            logger.debug(f'Removed old format: {file.name}')
                    except ValueError:
                        pass  # Not old format
    
    return deleted


def main():
    """Run the migration."""
    # Find images directory
    script_dir = Path(__file__).parent
    images_dir = script_dir.parent.parent / 'data' / 'images'
    
    if not images_dir.exists():
        logger.error(f'Images directory not found: {images_dir}')
        sys.exit(1)
    
    logger.info(f'Migrating images in: {images_dir}')
    
    # Get list of image files
    image_files = [f.name for f in images_dir.iterdir() if f.suffix in ('.png', '.jpg')]
    logger.info(f'Found {len(image_files)} image files')
    
    # Process each image
    total_created = 0
    processed = 0
    
    for filename in image_files:
        created = migrate_image(images_dir, filename)
        if created > 0:
            total_created += created
            processed += 1
            logger.info(f'  {filename} -> {created} variants created')
    
    logger.info(f'\nMigration complete:')
    logger.info(f'  - Processed: {processed} images')
    logger.info(f'  - Created: {total_created} variant files')
    
    # Ask about cleanup
    if processed > 0:
        logger.info(f'\nYou can optionally clean up old format files.')
        logger.info(f'Run with --cleanup to remove old timestamp-hash format files.')
        
        if '--cleanup' in sys.argv:
            deleted = cleanup_old_format(images_dir)
            logger.info(f'  - Deleted: {deleted} old format files')


if __name__ == '__main__':
    main()

