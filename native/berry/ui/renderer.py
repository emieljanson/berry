"""
Renderer - All drawing/rendering logic for the Berry UI.
"""
import logging
from typing import Optional, List, Dict, Tuple

import pygame

from .helpers import draw_aa_circle, draw_aa_rounded_rect
from .image_cache import ImageCache
from ..models import CatalogItem, NowPlaying
from ..config import (
    SCREEN_WIDTH, SCREEN_HEIGHT, COLORS,
    COVER_SIZE, COVER_SIZE_SMALL, COVER_SPACING,
    TRACK_INFO_Y, CAROUSEL_Y, CONTROLS_Y,
    BTN_SIZE, PLAY_BTN_SIZE, PROGRESS_BAR_HEIGHT,
    VOLUME_LEVELS,
)

logger = logging.getLogger(__name__)


class Renderer:
    """Handles all drawing/rendering for Berry UI."""
    
    def __init__(self, screen: pygame.Surface, image_cache: ImageCache, icons: Dict[str, pygame.Surface]):
        self.screen = screen
        self.image_cache = image_cache
        self.icons = icons
        
        # Fonts
        self.font_large = pygame.font.Font(None, 42)
        self.font_medium = pygame.font.Font(None, 32)
        self.font_small = pygame.font.Font(None, 24)
        
        # Caches
        self._bg_cache: Optional[pygame.Surface] = None
        self._progress_cache: Dict[str, pygame.Surface] = {}
        self._text_cache: Dict[str, pygame.Surface] = {}
        self._last_track_key: Optional[Tuple[str, str]] = None
        
        # Partial update state
        self._needs_full_redraw = True
        self._static_layer: Optional[pygame.Surface] = None
        self._carousel_rect = pygame.Rect(0, CAROUSEL_Y - 50, SCREEN_WIDTH, COVER_SIZE + 100)
        self._last_playing_state: Optional[bool] = None
        self._last_selected_index: Optional[int] = None
        
        # Button hit rectangles (updated during draw)
        self.add_button_rect: Optional[Tuple[int, int, int, int]] = None
        self.delete_button_rect: Optional[Tuple[int, int, int, int]] = None
    
    def invalidate(self):
        """Force a full redraw on next frame."""
        self._needs_full_redraw = True
    
    def draw(self, 
             items: List[CatalogItem],
             selected_index: int,
             now_playing: NowPlaying,
             scroll_x: float,
             drag_offset: float,
             dragging: bool,
             is_sleeping: bool,
             connected: bool,
             volume_index: int,
             delete_mode_id: Optional[str] = None) -> Optional[List[pygame.Rect]]:
        """
        Main draw method.
        
        Returns list of dirty rects for partial update, or None for full flip.
        """
        # Sleep mode - show black screen only
        if is_sleeping:
            self.screen.fill((0, 0, 0))
            self._needs_full_redraw = True
            return None
        
        # Clear button hit rects
        self.add_button_rect = None
        self.delete_button_rect = None
        
        # Check if we need a full redraw
        state_changed = (
            self._last_playing_state != now_playing.playing or
            self._last_selected_index != selected_index or
            self._last_track_key is None
        )
        
        if state_changed:
            self._needs_full_redraw = True
            self._last_playing_state = now_playing.playing
            self._last_selected_index = selected_index
        
        # Disconnected state
        if not connected:
            self._draw_background()
            self._draw_disconnected()
            self._needs_full_redraw = True
            return None
        
        # Empty state
        if not items:
            self._draw_background()
            self._draw_empty_state()
            self._needs_full_redraw = True
            return None
        
        # Get current item
        current_item = items[selected_index] if selected_index < len(items) else None
        
        # Calculate effective scroll position
        if dragging:
            drag_index_offset = -drag_offset / (COVER_SIZE + COVER_SPACING)
            effective_scroll = selected_index + drag_index_offset
        else:
            effective_scroll = scroll_x
        
        # Determine if animating
        is_animating = dragging or abs(scroll_x - selected_index) > 0.01
        
        if self._needs_full_redraw:
            # Full redraw
            self._draw_background()
            self._draw_track_info(current_item, now_playing)
            self._draw_controls(now_playing.playing, volume_index)
            
            # Cache static parts
            if self._static_layer is None:
                self._static_layer = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            self._static_layer.blit(self.screen, (0, 0))
            
            # Draw carousel
            self._draw_carousel(items, effective_scroll, now_playing, delete_mode_id)
            
            self._needs_full_redraw = False
            return None
        
        elif is_animating:
            # Partial update - only carousel area
            self.screen.blit(self._static_layer, 
                           self._carousel_rect.topleft, 
                           self._carousel_rect)
            self._draw_carousel(items, effective_scroll, now_playing, delete_mode_id)
            return [self._carousel_rect]
        
        else:
            # Idle - update progress bar if playing
            if now_playing.playing:
                self.screen.blit(self._static_layer,
                               self._carousel_rect.topleft,
                               self._carousel_rect)
                self._draw_carousel(items, effective_scroll, now_playing, delete_mode_id)
                return [self._carousel_rect]
            return []
    
    def _draw_background(self):
        """Draw pre-rendered background with gradient."""
        if not self._bg_cache:
            self._bg_cache = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            self._bg_cache.fill(COLORS['bg_primary'])
            # Pre-render gradient with purple accent tint
            for y in range(150):
                alpha = int(30 * (1 - y / 150))
                color = (
                    min(255, COLORS['bg_primary'][0] + int(alpha * 0.75)),
                    min(255, COLORS['bg_primary'][1] + int(alpha * 0.4)),
                    min(255, COLORS['bg_primary'][2] + alpha),
                )
                pygame.draw.line(self._bg_cache, color, (0, y), (SCREEN_WIDTH, y))
            self._bg_cache = self._bg_cache.convert()
        
        self.screen.blit(self._bg_cache, (0, 0))
    
    def _draw_disconnected(self):
        """Draw disconnected state."""
        text = self.font_large.render('Connecting to Berry...', True, COLORS['text_secondary'])
        rect = text.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2))
        self.screen.blit(text, rect)
    
    def _draw_empty_state(self):
        """Draw empty catalog state."""
        icon = self.font_large.render('🎧', True, COLORS['text_primary'])
        icon_rect = icon.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 - 40))
        self.screen.blit(icon, icon_rect)
        
        title = self.font_large.render('No music yet', True, COLORS['text_primary'])
        title_rect = title.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 20))
        self.screen.blit(title, title_rect)
        
        sub = self.font_medium.render('Play music via Spotify and tap + to add', True, COLORS['text_secondary'])
        sub_rect = sub.get_rect(center=(SCREEN_WIDTH // 2, SCREEN_HEIGHT // 2 + 60))
        self.screen.blit(sub, sub_rect)
    
    def _draw_track_info(self, item: Optional[CatalogItem], now_playing: NowPlaying):
        """Draw track name and artist."""
        if not item:
            return
        
        # Determine what to show
        if now_playing.context_uri == item.uri and now_playing.track_name:
            name = now_playing.track_name
            artist = now_playing.track_artist or ''
        elif item.current_track and isinstance(item.current_track, dict):
            name = item.current_track.get('name', item.name) or item.name
            artist = item.current_track.get('artist', item.artist or '') or item.artist or ''
        else:
            name = item.name or 'Unknown'
            artist = item.artist or ''
        
        # Check if text changed
        track_key = (name, artist)
        if track_key != self._last_track_key:
            self._last_track_key = track_key
            
            max_width = SCREEN_WIDTH - 100
            display_name = name
            
            name_surface = self.font_large.render(display_name, True, COLORS['text_primary'])
            if name_surface.get_width() > max_width:
                while name_surface.get_width() > max_width - 30 and len(display_name) > 3:
                    display_name = display_name[:-1]
                name_surface = self.font_large.render(display_name + '...', True, COLORS['text_primary'])
            
            self._text_cache['name_surface'] = name_surface
            self._text_cache['name_rect'] = name_surface.get_rect(center=(SCREEN_WIDTH // 2, TRACK_INFO_Y))
            
            if artist:
                artist_surface = self.font_medium.render(artist, True, COLORS['text_secondary'])
                self._text_cache['artist_surface'] = artist_surface
                self._text_cache['artist_rect'] = artist_surface.get_rect(center=(SCREEN_WIDTH // 2, TRACK_INFO_Y + 35))
            else:
                self._text_cache['artist_surface'] = None
        
        self.screen.blit(self._text_cache['name_surface'], self._text_cache['name_rect'])
        if self._text_cache.get('artist_surface'):
            self.screen.blit(self._text_cache['artist_surface'], self._text_cache['artist_rect'])
    
    def _draw_carousel(self, items: List[CatalogItem], scroll_x: float, 
                       now_playing: NowPlaying, delete_mode_id: Optional[str]):
        """Draw album cover carousel."""
        center_x = SCREEN_WIDTH // 2
        y = CAROUSEL_Y
        
        max_index = max(0, len(items) - 1)
        scroll_x = max(0, min(scroll_x, max_index))
        
        start_i = max(0, int(scroll_x) - 2)
        end_i = min(len(items), int(scroll_x) + 3)
        
        center_cover_rect = None
        center_item = None
        
        for i in range(start_i, end_i):
            item = items[i]
            offset = i - scroll_x
            x = center_x + offset * (COVER_SIZE + COVER_SPACING)
            
            is_center = abs(offset) < 0.5
            size = COVER_SIZE if is_center else COVER_SIZE_SMALL
            
            draw_x = int(x - size // 2)
            draw_y = y + (COVER_SIZE - size) // 2
            
            if draw_x + size < 0 or draw_x > SCREEN_WIDTH:
                continue
            
            is_playlist = item.type == 'playlist' or 'playlist' in (item.uri or '')
            has_multiple_images = item.images and len(item.images) > 1
            
            if is_center:
                if is_playlist and has_multiple_images:
                    cover = self.image_cache.get_composite(item.images, size)
                else:
                    cover = self.image_cache.get(item.image, size)
                center_cover_rect = (draw_x, draw_y, size, size)
                center_item = item
            else:
                if is_playlist and has_multiple_images:
                    cover = self.image_cache.get_composite_dimmed(item.images, size)
                else:
                    cover = self.image_cache.get_dimmed(item.image, size)
            
            self.screen.blit(cover, (draw_x, draw_y))
        
        if center_cover_rect and center_item:
            self._draw_cover_progress(center_cover_rect, center_item, now_playing)
            
            if center_item.is_temp:
                self._draw_add_button(center_cover_rect)
            elif delete_mode_id == center_item.id:
                self._draw_delete_button(center_cover_rect)
    
    def _draw_cover_progress(self, cover_rect: tuple, item: CatalogItem, now_playing: NowPlaying):
        """Draw progress bar at the bottom edge of the cover."""
        cover_x, cover_y, cover_w, cover_h = cover_rect
        
        if now_playing.context_uri != item.uri:
            return
        
        progress = now_playing.progress
        if progress <= 0:
            return
        
        bar_height = PROGRESS_BAR_HEIGHT
        fill_width = int(cover_w * min(progress, 1.0))
        
        if fill_width <= 0:
            return
        
        # Cache progress bar mask
        mask_key = f'_progress_mask_{cover_w}'
        if mask_key not in self._progress_cache:
            radius = max(12, cover_w // 25)
            mask = pygame.Surface((cover_w, cover_h), pygame.SRCALPHA)
            pygame.draw.rect(mask, (255, 255, 255, 255), (0, 0, cover_w, cover_h), border_radius=radius)
            self._progress_cache[mask_key] = mask
        
        # Reuse cached progress surface
        surf_key = f'_progress_surf_{cover_w}'
        if surf_key not in self._progress_cache:
            self._progress_cache[surf_key] = pygame.Surface((cover_w, cover_h), pygame.SRCALPHA)
        
        progress_surf = self._progress_cache[surf_key]
        progress_surf.fill((0, 0, 0, 0))
        
        pygame.draw.rect(progress_surf, COLORS['accent'],
                        (0, cover_h - bar_height, fill_width, bar_height))
        
        progress_surf.blit(self._progress_cache[mask_key], (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        self.screen.blit(progress_surf, (cover_x, cover_y))
    
    def _draw_controls(self, is_playing: bool, volume_index: int):
        """Draw playback control buttons."""
        center_x = SCREEN_WIDTH // 2
        y = CONTROLS_Y
        btn_spacing = 145
        
        # Prev button
        prev_center = (center_x - btn_spacing, y)
        draw_aa_circle(self.screen, COLORS['bg_elevated'], prev_center, BTN_SIZE // 2)
        self._draw_icon('prev', prev_center)
        
        # Play/Pause button
        play_center = (center_x, y)
        draw_aa_circle(self.screen, COLORS['accent'], play_center, PLAY_BTN_SIZE // 2)
        self._draw_icon('pause' if is_playing else 'play', play_center)
        
        # Next button
        next_center = (center_x + btn_spacing, y)
        draw_aa_circle(self.screen, COLORS['bg_elevated'], next_center, BTN_SIZE // 2)
        self._draw_icon('next', next_center)
        
        # Volume button
        right_cover_edge = center_x + (COVER_SIZE + COVER_SPACING) + COVER_SIZE_SMALL // 2
        vol_center = (right_cover_edge - BTN_SIZE // 2, y)
        draw_aa_circle(self.screen, COLORS['bg_elevated'], vol_center, BTN_SIZE // 2)
        icon_key = VOLUME_LEVELS[volume_index]['icon']
        self._draw_icon(icon_key, vol_center)
    
    def _draw_icon(self, name: str, center: tuple):
        """Draw an icon centered at position."""
        icon = self.icons.get(name)
        if icon:
            rect = icon.get_rect(center=center)
            self.screen.blit(icon, rect)
    
    def _draw_add_button(self, cover_rect: tuple):
        """Draw + button on cover for temp items."""
        cover_x, cover_y, cover_w, cover_h = cover_rect
        
        btn_size = 100
        icon_size = 72
        margin = 16
        btn_x = cover_x + cover_w - btn_size - margin
        btn_y = cover_y + cover_h - btn_size - margin
        center = (btn_x + btn_size // 2, btn_y + btn_size // 2)
        
        icon = self.icons.get('plus')
        if icon:
            scaled_icon = pygame.transform.smoothscale(icon, (icon_size, icon_size))
            tinted = scaled_icon.copy()
            tinted.fill(COLORS['accent'], special_flags=pygame.BLEND_RGB_MULT)
            icon_rect = tinted.get_rect(center=center)
            self.screen.blit(tinted, icon_rect)
        
        self.add_button_rect = (btn_x, btn_y, btn_size, btn_size)
    
    def _draw_delete_button(self, cover_rect: tuple):
        """Draw - button on cover for delete mode."""
        cover_x, cover_y, cover_w, cover_h = cover_rect
        
        btn_size = 100
        icon_size = 72
        margin = 16
        btn_x = cover_x + cover_w - btn_size - margin
        btn_y = cover_y + cover_h - btn_size - margin
        center = (btn_x + btn_size // 2, btn_y + btn_size // 2)
        
        icon = self.icons.get('minus')
        if icon:
            scaled_icon = pygame.transform.smoothscale(icon, (icon_size, icon_size))
            tinted = scaled_icon.copy()
            tinted.fill(COLORS['error'], special_flags=pygame.BLEND_RGB_MULT)
            icon_rect = tinted.get_rect(center=center)
            self.screen.blit(tinted, icon_rect)
        
        self.delete_button_rect = (btn_x, btn_y, btn_size, btn_size)

