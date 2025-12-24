#!/usr/bin/env python3
"""
Self-contained Editing Feature Module

This module provides Selection and InputController classes for text editing.
Following the pattern of find_feature.py and undo_redo_feature.py.

Usage:
    from editing_feature import Selection, InputController
    
    # Use in your editor
    buffer.selection = Selection()
    controller = InputController(view, buffer)
    controller.move_up(extend_selection=True)
"""

import unicodedata

# Try to import GTK/Cairo dependencies
try:
    import cairo
    import gi
    gi.require_version('Pango', '1.0')
    from gi.repository import Pango
    EDITING_FEATURES_AVAILABLE = True
except ImportError:
    EDITING_FEATURES_AVAILABLE = False
    print("Warning: GTK/Cairo/Pango not available - editing features limited")


# ============================================================
#   HELPER FUNCTIONS
# ============================================================

def detect_rtl_line(text):
    """Detect if a line is RTL using Unicode bidirectional properties.
    
    Returns True  if the first strong directional character is RTL,
    False if LTR, or False if no strong directional characters found.
    """
    for ch in text:
        t = unicodedata.bidirectional(ch)
        if t in ("L", "LRE", "LRO"):
            return False
        if t in ("R", "AL", "RLE", "RLO"):
            return True
    return False



# ============================================================
#   SELECTION CLASS
# ============================================================

class Selection:
    """Manages text selection state"""
    
    def __init__(self):
        self.start_line = -1
        self.start_col = -1
        self.end_line = -1
        self.end_col = -1
        self.active = False
        self.selecting_with_keyboard = False
    
    def clear(self):
        """Clear the selection"""
        self.start_line = -1
        self.start_col = -1
        self.end_line = -1
        self.end_col = -1
        self.active = False
        self.selecting_with_keyboard = False
    
    def set_wrap_enabled(self, enabled):
        """Enable or disable word wrap."""
        if self.wrap_enabled == enabled:
            return
        
        self.wrap_enabled = enabled
        self.wrap_cache = {}
        self.visual_line_map = []
        # self.total_visual_lines_cache = None
        self.visual_line_anchor = (0, 0)

    def set_start(self, line, col):
        """Set selection start point"""
        self.start_line = line
        self.start_col = col
        self.end_line = line
        self.end_col = col
        self.active = True
    
    def set_end(self, line, col):
        """Set selection end point"""
        self.end_line = line
        self.end_col = col
        self.active = (self.start_line != self.end_line or self.start_col != self.end_col)
    
    def has_selection(self):
        """Check if there's an active selection"""
        return self.active and (
            self.start_line != self.end_line or 
            self.start_col != self.end_col
        )
    
    def get_bounds(self):
        """Get normalized selection bounds (start always before end)"""
        if not self.has_selection():
            return None, None, None, None
            
        # Normalize so start is always before end
        if self.start_line < self.end_line:
            return self.start_line, self.start_col, self.end_line, self.end_col
        elif self.start_line > self.end_line:
            return self.end_line, self.end_col, self.start_line, self.start_col
        else:
            # Same line
            if self.start_col <= self.end_col:
                return self.start_line, self.start_col, self.end_line, self.end_col
            else:
                return self.end_line, self.end_col, self.start_line, self.start_col
    
    def contains_position(self, line, col):
        """Check if a position is within the selection"""
        if not self.has_selection():
            return False
            
        start_line, start_col, end_line, end_col = self.get_bounds()
        
        if line < start_line or line > end_line:
            return False
        
        if line == start_line and line == end_line:
            return start_col <= col <= end_col
        elif line == start_line:
            return col >= start_col
        elif line == end_line:
            return col <= end_col
        else:
            return True



# ============================================================
#   INPUT CONTROLLER CLASS
# ============================================================

class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf = buf
        self.dragging = False
        self.drag_start_line = -1
        self.drag_start_col = -1

    def click(self, ln, col):
        self.buf.set_cursor(ln, col)
        self.buf.selection.clear()
        self.drag_start_line = ln
        self.drag_start_col = col
        self.dragging = False

    def start_drag(self, ln, col):
        self.dragging = True
        self.drag_start_line = ln
        self.drag_start_col = col
        
        # Set cursor first (this clears old selection and sets cursor position)
        self.buf.set_cursor(ln, col, extend_selection=False)
        
        # Now establish the new selection anchor at the current cursor position
        self.buf.selection.set_start(ln, col)
        self.buf.selection.set_end(ln, col)

    def update_drag(self, ln, col):
        if self.dragging:
            self.buf.selection.set_end(ln, col)
            self.buf.set_cursor(ln, col, extend_selection=True)

    def end_drag(self):
        """End drag selection"""
        self.dragging = False

    def move_left(self, extend_selection=False):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        
        if not extend_selection and b.selection.has_selection():
            # Move to start of selection
            start_ln, start_col, _, _ = b.selection.get_bounds()
            b.set_cursor(start_ln, start_col, extend_selection)
        elif col > 0:
            # Move left within line
            b.set_cursor(ln, col - 1, extend_selection)
        elif ln > 0:
            # At start of line - move to end of previous line (selecting the newline)
            prev = b.get_line(ln - 1)
            b.set_cursor(ln - 1, len(prev), extend_selection)

    def move_right(self, extend_selection=False):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        line = b.get_line(ln)
        
        if not extend_selection and b.selection.has_selection():
            # Move to end of selection
            _, _, end_ln, end_col = b.selection.get_bounds()
            b.set_cursor(end_ln, end_col, extend_selection)
        elif col < len(line):
            # Move right within line
            b.set_cursor(ln, col + 1, extend_selection)
        elif ln + 1 < b.total():
            # At end of line - move to start of next line (selecting the newline)
            b.set_cursor(ln + 1, 0, extend_selection)

    def move_up(self, extend_selection=False):
        b = self.buf
        ln = b.cursor_line
        
        # If there's a selection and not extending, move to start of selection
        if not extend_selection and b.selection.has_selection():
            start_ln, start_col, _, _ = b.selection.get_bounds()
            b.set_cursor(start_ln, start_col, extend_selection)
            return
        
        # Visual line movement when wrapping enabled
        if self.view.renderer.wrap_enabled:
            # Create cairo context for calculations
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            ln_w = self.view.renderer.calculate_line_number_width(cr, b.total())
            alloc_w = self.view.get_width()
            
            # Get wrap points for current line
            wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, ln, ln_w, alloc_w)
            
            # Find which visual sub-line the cursor is on
            vis_idx = 0
            for i, (start, end) in enumerate(wrap_points):
                if start <= b.cursor_col <= end:
                    vis_idx = i
                    break
            
            # Calculate current visual x offset
            full_text = b.get_line(ln)
            start_col, end_col = wrap_points[vis_idx]
            
            if end_col > start_col:
                text_segment = full_text[start_col:end_col]
            else:
                text_segment = full_text[start_col:] if start_col < len(full_text) else ""
                
            col_in_segment = b.cursor_col - start_col
            
            layout = self.view.renderer.create_text_layout(cr, text_segment)
            is_rtl = detect_rtl_line(text_segment)
            text_w = self.view.renderer.get_text_width(cr, text_segment)
            base_x = self.view.renderer.calculate_text_base_x(is_rtl, text_w, alloc_w, ln_w, self.view.scroll_x)
            
            # Get pixel position of cursor
            def visual_byte_index(text, col):
                b = 0
                for ch in text[:col]:
                    b += len(ch.encode("utf-8"))
                return b
                
            idx = visual_byte_index(text_segment, col_in_segment)
            pos, _ = layout.get_cursor_pos(idx)
            cursor_x = base_x + (pos.x // Pango.SCALE)
            
            # Determine target line and visual index
            target_ln = ln
            target_vis_idx = vis_idx - 1
            
            if target_vis_idx < 0:
                # Move to previous logical line
                target_ln = ln - 1
                if target_ln >= 0:
                    target_wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, target_ln, ln_w, alloc_w)
                    target_vis_idx = len(target_wrap_points) - 1
                else:
                    # Start of file
                    if extend_selection:
                        b.set_cursor(0, 0, extend_selection)
                    return

            # Get wrap points for target line (if different)
            if target_ln != ln:
                target_wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, target_ln, ln_w, alloc_w)
            else:
                target_wrap_points = wrap_points
                
            # Get text segment for target visual line
            t_start, t_end = target_wrap_points[target_vis_idx]
            t_full_text = b.get_line(target_ln)
            
            if t_end > t_start:
                t_segment = t_full_text[t_start:t_end]
            else:
                t_segment = t_full_text[t_start:] if t_start < len(t_full_text) else ""
            
            # Find column in target segment closest to cursor_x
            t_is_rtl = detect_rtl_line(t_segment)
            t_text_w = self.view.renderer.get_text_width(cr, t_segment)
            t_base_x = self.view.renderer.calculate_text_base_x(t_is_rtl, t_text_w, alloc_w, ln_w, self.view.scroll_x)
            
            rel_x = cursor_x - t_base_x
            new_col_in_segment = self.view.pixel_to_column(cr, t_segment, rel_x)
            new_col_in_segment = max(0, min(new_col_in_segment, len(t_segment)))
            
            new_col = t_start + new_col_in_segment
            b.set_cursor(target_ln, new_col, extend_selection)
            return

        if ln > 0:
            # Can move up to previous line
            target = ln - 1
            target_line = b.get_line(target)
            
            if extend_selection:
                # When extending selection upward
                # Check if target is an empty line
                if len(target_line) == 0:
                    # Moving up to an empty line - go to position 0
                    b.set_cursor(target, 0, extend_selection)
                else:
                    # Normal selection - maintain column position if possible
                    new_col = min(b.cursor_col, len(target_line))
                    b.set_cursor(target, new_col, extend_selection)
            else:
                # Not extending selection - normal movement
                new_col = min(b.cursor_col, len(target_line))
                b.set_cursor(target, new_col, extend_selection)
        else:
            # Already on first line (line 0), can't move up
            # If extending selection, select to beginning of current line (like shift+home)
            if extend_selection:
                b.set_cursor(0, 0, extend_selection)

    def move_down(self, extend_selection=False):
        b = self.buf
        ln = b.cursor_line
        
        # If there's a selection and not extending, move to end of selection
        if not extend_selection and b.selection.has_selection():
            _, _, end_ln, end_col = b.selection.get_bounds()
            b.set_cursor(end_ln, end_col, extend_selection)
            return
        
        # Visual line movement when wrapping enabled
        if self.view.renderer.wrap_enabled:
            # Create cairo context for calculations
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)
            ln_w = self.view.renderer.calculate_line_number_width(cr, b.total())
            alloc_w = self.view.get_width()
            
            # Get wrap points for current line
            wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, ln, ln_w, alloc_w)
            
            # Find which visual sub-line the cursor is on
            vis_idx = 0
            for i, (start, end) in enumerate(wrap_points):
                if start <= b.cursor_col <= end:
                    vis_idx = i
                    break
            
            # Calculate current visual x offset
            full_text = b.get_line(ln)
            start_col, end_col = wrap_points[vis_idx]
            
            if end_col > start_col:
                text_segment = full_text[start_col:end_col]
            else:
                text_segment = full_text[start_col:] if start_col < len(full_text) else ""
                
            col_in_segment = b.cursor_col - start_col
            
            layout = self.view.renderer.create_text_layout(cr, text_segment)
            is_rtl = detect_rtl_line(text_segment)
            text_w = self.view.renderer.get_text_width(cr, text_segment)
            base_x = self.view.renderer.calculate_text_base_x(is_rtl, text_w, alloc_w, ln_w, self.view.scroll_x)
            
            # Get pixel position of cursor
            def visual_byte_index(text, col):
                b = 0
                for ch in text[:col]:
                    b += len(ch.encode("utf-8"))
                return b
                
            idx = visual_byte_index(text_segment, col_in_segment)
            pos, _ = layout.get_cursor_pos(idx)
            cursor_x = base_x + (pos.x // Pango.SCALE)
            
            # Determine target line and visual index
            target_ln = ln
            target_vis_idx = vis_idx + 1
            
            if target_vis_idx >= len(wrap_points):
                # Move to next logical line
                target_ln = ln + 1
                target_vis_idx = 0
            
            if target_ln < b.total():
                # Get wrap points for target line (if different)
                if target_ln != ln:
                    target_wrap_points = self.view.renderer.get_wrap_points_for_line(cr, b, target_ln, ln_w, alloc_w)
                else:
                    target_wrap_points = wrap_points
                
                # Get text segment for target visual line
                t_start, t_end = target_wrap_points[target_vis_idx]
                t_full_text = b.get_line(target_ln)
                
                if t_end > t_start:
                    t_segment = t_full_text[t_start:t_end]
                else:
                    t_segment = t_full_text[t_start:] if t_start < len(t_full_text) else ""
                
                # Find column in target segment closest to cursor_x
                t_is_rtl = detect_rtl_line(t_segment)
                t_text_w = self.view.renderer.get_text_width(cr, t_segment)
                t_base_x = self.view.renderer.calculate_text_base_x(t_is_rtl, t_text_w, alloc_w, ln_w, self.view.scroll_x)
                
                rel_x = cursor_x - t_base_x
                new_col_in_segment = self.view.pixel_to_column(cr, t_segment, rel_x)
                new_col_in_segment = max(0, min(new_col_in_segment, len(t_segment)))
                
                new_col = t_start + new_col_in_segment
                b.set_cursor(target_ln, new_col, extend_selection)
                return
            elif extend_selection:
                # At end of file, select to end
                current_line = b.get_line(ln)
                b.set_cursor(ln, len(current_line), extend_selection)
                return

        if ln + 1 < b.total():
            # Can move down to next line
            target = ln + 1
            target_line = b.get_line(target)
            
            if extend_selection:
                # When extending selection downward
                current_line = b.get_line(ln)
                
                # Check if target is the last line (no newline after it)
                is_last_line = (target == b.total() - 1)
                
                # Special case: at column 0 of empty line
                if len(current_line) == 0 and b.cursor_col == 0:
                    if is_last_line:
                        # Empty line followed by last line - select to end of last line
                        b.set_cursor(target, len(target_line), extend_selection)
                    else:
                        # Empty line with more lines after - select just the newline
                        b.set_cursor(target, 0, extend_selection)
                else:
                    # Normal selection - maintain column position
                    new_col = min(b.cursor_col, len(target_line))
                    b.set_cursor(target, new_col, extend_selection)
            else:
                # Not extending selection - normal movement
                new_col = min(b.cursor_col, len(target_line))
                b.set_cursor(target, new_col, extend_selection)
        else:
            # Already on last line, can't move down
            # If extending selection, select to end of current line (like shift+end)
            if extend_selection:
                current_line = b.get_line(ln)
                b.set_cursor(ln, len(current_line), extend_selection)

    def move_word_left(self, extend_selection=False):
        """Move cursor to the start of the previous word"""
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        line = b.get_line(ln)
        
        # Helper to check if character is a word character
        import unicodedata
        def is_word_char(ch):
            if ch == '_':
                return True
            cat = unicodedata.category(ch)
            return cat[0] in ('L', 'N', 'M')
        
        # If at start of line, go to end of previous line
        if col == 0:
            if ln > 0:
                prev_line = b.get_line(ln - 1)
                b.set_cursor(ln - 1, len(prev_line), extend_selection)
            return
        
        # Skip whitespace to the left
        while col > 0 and line[col - 1].isspace():
            col -= 1
        
        if col == 0:
            b.set_cursor(ln, col, extend_selection)
            return
        
        # Now we're on a non-whitespace character
        # Check what type it is and skip that type
        if is_word_char(line[col - 1]):
            # Skip word characters to the left
            while col > 0 and is_word_char(line[col - 1]):
                col -= 1
        else:
            # Skip symbols/punctuation to the left (treat as a "word")
            while col > 0 and not line[col - 1].isspace() and not is_word_char(line[col - 1]):
                col -= 1
        
        b.set_cursor(ln, col, extend_selection)
    
    def move_word_right(self, extend_selection=False):
        """Move cursor to the start of the next word"""
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        line = b.get_line(ln)
        
        # Helper to check if character is a word character
        import unicodedata
        def is_word_char(ch):
            if ch == '_':
                return True
            cat = unicodedata.category(ch)
            return cat[0] in ('L', 'N', 'M')
        
        # If at end of line, go to start of next line
        if col >= len(line):
            if ln + 1 < b.total():
                b.set_cursor(ln + 1, 0, extend_selection)
            return
        
        # Special handling when cursor is on space with no selection
        if line[col].isspace() and not b.selection.has_selection():
            # Select space(s) + next word
            start_col = col
            
            # Skip whitespace on current line
            while col < len(line) and line[col].isspace():
                col += 1
            
            # If we reached end of line
            if col >= len(line):
                # Check if there's a next line
                if ln + 1 < b.total():
                    # Select space(s) + newline + next word from next line
                    next_line = b.get_line(ln + 1)
                    next_col = 0
                    
                    # Skip leading whitespace on next line
                    while next_col < len(next_line) and next_line[next_col].isspace():
                        next_col += 1
                    
                    # Select the next word on next line
                    if next_col < len(next_line):
                        if is_word_char(next_line[next_col]):
                            while next_col < len(next_line) and is_word_char(next_line[next_col]):
                                next_col += 1
                        elif not next_line[next_col].isspace():
                            while next_col < len(next_line) and not next_line[next_col].isspace() and not is_word_char(next_line[next_col]):
                                next_col += 1
                    
                    # Set selection from start_col on current line to next_col on next line
                    b.selection.set_start(ln, start_col)
                    b.selection.set_end(ln + 1, next_col)
                    b.cursor_line = ln + 1
                    b.cursor_col = next_col
                    return
                else:
                    # No next line - select spaces to end of line
                    b.selection.set_start(ln, start_col)
                    b.selection.set_end(ln, col)
                    b.cursor_col = col
                    return
            
            # We found a non-space character - select the word
            if is_word_char(line[col]):
                while col < len(line) and is_word_char(line[col]):
                    col += 1
            elif not line[col].isspace():
                while col < len(line) and not line[col].isspace() and not is_word_char(line[col]):
                    col += 1
            
            # Set selection from start_col to col
            b.selection.set_start(ln, start_col)
            b.selection.set_end(ln, col)
            b.cursor_col = col
            return
        
        # Check what type of character we're on and skip that type
        if is_word_char(line[col]):
            # Skip word characters to the right
            while col < len(line) and is_word_char(line[col]):
                col += 1
        elif not line[col].isspace():
            # Skip symbols/punctuation to the right (treat as a "word")
            while col < len(line) and not line[col].isspace() and not is_word_char(line[col]):
                col += 1
        
        # If extending an existing selection, skip whitespace AND select next word
        # This makes second Ctrl+Shift+Right select space + next word
        if extend_selection and b.selection.has_selection():
            # Skip whitespace
            while col < len(line) and line[col].isspace():
                col += 1
            
            # Now select the next word
            if col < len(line):
                if is_word_char(line[col]):
                    while col < len(line) and is_word_char(line[col]):
                        col += 1
                elif not line[col].isspace():
                    while col < len(line) and not line[col].isspace() and not is_word_char(line[col]):
                        col += 1
        
        b.set_cursor(ln, col, extend_selection)
    def move_home(self, extend_selection=False):
        """Move to beginning of line"""
        b = self.buf
        b.set_cursor(b.cursor_line, 0, extend_selection)

    def move_end(self, extend_selection=False):
        """Move to end of line"""
        b = self.buf
        line = b.get_line(b.cursor_line)
        b.set_cursor(b.cursor_line, len(line), extend_selection)

    def move_document_start(self, extend_selection=False):
        """Move to beginning of document"""
        self.buf.set_cursor(0, 0, extend_selection)

    def move_document_end(self, extend_selection=False):
        """Move to end of document"""
        b = self.buf
        total = b.total()
        last_line = total - 1
        last_line_text = b.get_line(last_line)
        b.set_cursor(last_line, len(last_line_text), extend_selection)



# ============================================================
#   INSTALLATION / INTEGRATION
# ============================================================

def has_editing_support():
    """Check if all dependencies are available"""
    return EDITING_FEATURES_AVAILABLE


def install_editing_feature(buffer_class, view_class=None):
    """
    Validate that classes have required interfaces.
    
    Args:
        buffer_class: The buffer class to validate
        view_class: Optional view class to validate
        
    Returns:
        True if classes have required interfaces
    """
    # Check buffer
    required_buffer_methods = ['total', 'get_line', 'set_cursor']
    for method in required_buffer_methods:
        if not hasattr(buffer_class, method):
            print(f"Warning: Buffer missing method: {method}")
            return False
    
    return True


# Export public API
__all__ = [
    'Selection',
    'InputController',
    'detect_rtl_line',
    'install_editing_feature',
    'has_editing_support',
    'EDITING_FEATURES_AVAILABLE'
]
