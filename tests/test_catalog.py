"""
Tests for CatalogManager - save/load, atomic writes, deduplication.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from berry.api.catalog import CatalogManager


class TestCatalogLoadSave:
    """Tests for catalog load/save operations."""

    def test_load_empty_catalog(self, catalog_path, images_path):
        """Loading non-existent catalog returns empty list."""
        manager = CatalogManager(catalog_path, images_path)
        items = manager.load()
        assert items == []

    def test_load_existing_catalog(self, catalog_with_file, images_path, sample_catalog_data):
        """Loading existing catalog returns all items."""
        manager = CatalogManager(catalog_with_file, images_path)
        items = manager.load()
        assert len(items) == 2
        assert items[0].name == 'Test Album 1'
        assert items[1].name == 'Test Playlist'

    def test_save_and_reload(self, catalog_path, images_path):
        """Saving and reloading preserves item data."""
        manager = CatalogManager(catalog_path, images_path)
        manager.load()

        # Save a new item
        item_data = {
            'type': 'album',
            'uri': 'spotify:album:new',
            'name': 'New Album',
            'artist': 'New Artist',
            'image': None,
        }
        success = manager.save_item(item_data)
        assert success

        # Reload and verify
        manager2 = CatalogManager(catalog_path, images_path)
        items = manager2.load()
        assert len(items) == 1
        assert items[0].name == 'New Album'
        assert items[0].uri == 'spotify:album:new'

    def test_delete_item(self, catalog_with_file, images_path):
        """Deleting item removes it from catalog."""
        manager = CatalogManager(catalog_with_file, images_path)
        manager.load()
        assert len(manager.items) == 2

        # Delete first item
        success = manager.delete_item('1')
        assert success

        # Reload and verify
        manager2 = CatalogManager(catalog_with_file, images_path)
        items = manager2.load()
        assert len(items) == 1
        assert items[0].id == '2'

    def test_duplicate_uri_rejected(self, catalog_with_file, images_path):
        """Saving duplicate URI is rejected."""
        manager = CatalogManager(catalog_with_file, images_path)
        manager.load()

        item_data = {
            'type': 'album',
            'uri': 'spotify:album:test1',  # Already exists
            'name': 'Duplicate Album',
            'artist': 'Artist',
            'image': None,
        }
        success = manager.save_item(item_data)
        assert not success


class TestAtomicWrites:
    """Tests for atomic file write functionality."""

    def test_atomic_write_creates_file(self, catalog_path, images_path):
        """Atomic write creates catalog file correctly."""
        manager = CatalogManager(catalog_path, images_path)
        manager.load()

        item_data = {
            'type': 'album',
            'uri': 'spotify:album:atomic',
            'name': 'Atomic Album',
            'artist': 'Artist',
            'image': None,
        }
        manager.save_item(item_data)

        # Verify file exists and is valid JSON
        assert catalog_path.exists()
        data = json.loads(catalog_path.read_text())
        assert 'items' in data
        assert len(data['items']) == 1

    def test_no_temp_file_after_save(self, catalog_path, images_path):
        """Temp file is cleaned up after successful save."""
        manager = CatalogManager(catalog_path, images_path)
        manager.load()

        item_data = {
            'type': 'album',
            'uri': 'spotify:album:test',
            'name': 'Test',
            'artist': 'Artist',
            'image': None,
        }
        manager.save_item(item_data)

        # Verify no temp file left behind
        temp_path = catalog_path.with_suffix('.json.tmp')
        assert not temp_path.exists()

    def test_recovery_from_temp_file(self, catalog_path, images_path):
        """Recovery from leftover temp file on startup."""
        # Create a temp file simulating crashed save
        temp_path = catalog_path.with_suffix('.json.tmp')
        temp_data = {
            'items': [
                {'id': 'recovered', 'uri': 'spotify:album:recovered',
                 'name': 'Recovered Album', 'type': 'album'}
            ]
        }
        temp_path.write_text(json.dumps(temp_data))

        # Load should recover from temp file
        manager = CatalogManager(catalog_path, images_path)
        items = manager.load()

        # Should have recovered the item
        assert len(items) == 1
        assert items[0].name == 'Recovered Album'

        # Temp file should be gone
        assert not temp_path.exists()
        # Main file should exist
        assert catalog_path.exists()


class TestProgressTracking:
    """Tests for playback progress tracking."""

    def test_save_and_get_progress(self, catalog_with_file, images_path):
        """Progress is saved and retrieved correctly."""
        manager = CatalogManager(catalog_with_file, images_path)
        manager.load()

        # Save progress
        manager.save_progress(
            context_uri='spotify:album:test1',
            track_uri='spotify:track:123',
            position=60000,
            track_name='Test Track',
            artist='Test Artist'
        )

        # Get progress
        progress = manager.get_progress('spotify:album:test1')
        assert progress is not None
        assert progress['uri'] == 'spotify:track:123'
        assert progress['position'] == 60000
        assert progress['name'] == 'Test Track'

    def test_clear_progress(self, catalog_with_file, images_path):
        """Progress can be cleared."""
        manager = CatalogManager(catalog_with_file, images_path)
        manager.load()

        # Save and then clear
        manager.save_progress('spotify:album:test1', 'spotify:track:123', 60000)
        manager.clear_progress('spotify:album:test1')

        progress = manager.get_progress('spotify:album:test1')
        assert progress is None

    def test_progress_for_unknown_context(self, catalog_with_file, images_path):
        """Getting progress for unknown context returns None."""
        manager = CatalogManager(catalog_with_file, images_path)
        manager.load()

        progress = manager.get_progress('spotify:album:unknown')
        assert progress is None


class TestMockMode:
    """Tests for mock mode behavior."""

    def test_mock_mode_returns_mock_data(self, catalog_path, images_path):
        """Mock mode returns predefined test data."""
        manager = CatalogManager(catalog_path, images_path, mock_mode=True)
        items = manager.load()

        assert len(items) > 0
        assert items[0].name == 'Abbey Road'

    def test_mock_mode_save_returns_true(self, catalog_path, images_path):
        """Save in mock mode always succeeds but does nothing."""
        manager = CatalogManager(catalog_path, images_path, mock_mode=True)
        manager.load()

        result = manager.save_item({'uri': 'test', 'name': 'Test'})
        assert result is True

        # File should not be created
        assert not catalog_path.exists()
