"""
UI Helpers - Drawing utilities for pygame.
"""
import pygame
import pygame.gfxdraw


def draw_aa_circle(surface: pygame.Surface, color: tuple, center: tuple, radius: int):
    """Draw an anti-aliased filled circle."""
    cx, cy = int(center[0]), int(center[1])
    r = int(radius)
    pygame.gfxdraw.aacircle(surface, cx, cy, r, color)
    pygame.gfxdraw.filled_circle(surface, cx, cy, r, color)


def draw_aa_rounded_rect(surface: pygame.Surface, color: tuple, rect: tuple, radius: int):
    """Draw an anti-aliased rounded rectangle using circles for corners."""
    x, y, w, h = rect
    r = min(radius, w // 2, h // 2)
    
    # If it's basically a circle (width == height and radius >= half), draw as circle
    if w == h and r >= w // 2:
        draw_aa_circle(surface, color, (x + w // 2, y + h // 2), w // 2)
        return
    
    # Draw the main rectangles (center cross)
    pygame.draw.rect(surface, color, (x + r, y, w - 2 * r, h))  # horizontal
    pygame.draw.rect(surface, color, (x, y + r, w, h - 2 * r))  # vertical
    
    # Draw anti-aliased corner circles
    corners = [
        (x + r, y + r),           # top-left
        (x + w - r - 1, y + r),   # top-right
        (x + r, y + h - r - 1),   # bottom-left
        (x + w - r - 1, y + h - r - 1)  # bottom-right
    ]
    for cx, cy in corners:
        pygame.gfxdraw.aacircle(surface, int(cx), int(cy), r, color)
        pygame.gfxdraw.filled_circle(surface, int(cx), int(cy), r, color)

