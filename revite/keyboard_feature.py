#!/usr/bin/env python3
"""
Self-contained Keyboard Feature Module

Provides keyboard event handling for text editing.
Following the pattern of find_feature.py and undo_redo_feature.py.

Usage:
    from keyboard_feature import KeyboardHandler
    
    # In your view class
    handler = KeyboardHandler(view, buffer, input_controller)
    handler.on_key(controller, keyval, keycode, state)
"""

try:
    import gi
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk
    import cairo
    KEYBOARD_FEATURES_AVAILABLE = True
except ImportError:
    KEYBOARD_FEATURES_AVAILABLE = False
    Gdk = None
    cairo = None


class KeyboardHandler:
    """Handles keyboard events for text editing"""
    
    def __init__(self, view, buf, input_controller):
        """
        Initialize keyboard handler
        
        Args:
            view: The view object (needs renderer, im, scrolling methods)
            buf: The buffer object (needs editing methods)
            input_controller: The InputController for navigation
        """
        self.view = view
        self.buf = buf
        self.ctrl = input_controller
    
    def on_key(self, c, keyval, keycode, state):
        # Let IM filter the event FIRST
        event = c.get_current_event()
        if event and self.im.filter_keypress(event):
            return True

        name = Gdk.keyval_name(keyval)
        shift_pressed = (state & Gdk.ModifierType.SHIFT_MASK) != 0
        ctrl_pressed = (state & Gdk.ModifierType.CONTROL_MASK) != 0
        alt_pressed = (state & Gdk.ModifierType.ALT_MASK) != 0

        # Undo (Ctrl+Z)
        if ctrl_pressed and not shift_pressed and not alt_pressed and (name == "z" or name == "Z"):
            self.buf.undo()
            
            # Clear wrap cache and update scrollbar before scrolling
            # This ensures visual line calculations are accurate
            if self.renderer.wrap_enabled:
                self.renderer.wrap_cache.clear()
                self.renderer.total_visual_lines_cache = None
            
            self.update_scrollbar()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
            
        # Redo (Ctrl+Y or Ctrl+Shift+Z)
        if ctrl_pressed and \
           ((not shift_pressed and (name == "y" or name == "Y")) or \
            (shift_pressed and (name == "z" or name == "Z"))):
            self.buf.redo()
            
            # Clear wrap cache and update scrollbar before scrolling
            # This ensures visual line calculations are accurate
            if self.renderer.wrap_enabled:
                self.renderer.wrap_cache.clear()
                self.renderer.total_visual_lines_cache = None
            
            self.update_scrollbar()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Alt+Z - Toggle word wrap
        if alt_pressed and (name == "z" or name == "Z"):
            saved_cursor_line = self.buf.cursor_line
            saved_cursor_col = self.buf.cursor_col

            # Save previous estimate
            width = self.get_width()
            height = self.get_height()
            previous_estimated_total = None
            if self.renderer.wrap_enabled and width > 0 and height > 0:
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                total_lines = self.buf.total()
                ln_width = self.renderer.calculate_line_number_width(cr, total_lines)
                viewport_width = width
                previous_estimated_total = self.renderer.get_total_visual_lines(
                    cr, self.buf, ln_width, viewport_width
                )

            self.renderer.wrap_enabled = not self.renderer.wrap_enabled

            # Clear wrap caches
            self.renderer.wrap_cache = {}
            self.renderer.visual_line_map = []
            self.renderer.total_visual_lines_locked = False
            self.renderer.visual_line_anchor = (0, 0)

            visible_lines = max(1, height // self.renderer.line_h) if height > 0 else 50
            total_lines = self.buf.total()

            if self.renderer.wrap_enabled:
                # Enabling wrap mode
                self.renderer.max_line_width = 0
                self.scroll_x = 0
                self.scroll_visual_offset = 0
                self.hadj.set_value(0)

                if width > 0 and height > 0 and total_lines > 0:
                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                    cr = cairo.Context(surface)
                    ln_width = self.renderer.calculate_line_number_width(cr, total_lines)

                    # Near EOF
                    if saved_cursor_line > total_lines * 0.8:
                        buffer = visible_lines * 3
                        start_line = max(0, saved_cursor_line - buffer)
                        end_line = total_lines
                        for ln in range(start_line, end_line):
                            self.renderer.get_wrap_points_for_line(
                                cr, self.buf, ln, ln_width, width
                            )

                        saved_cache = self.renderer.wrap_cache.copy()
                        self.renderer.wrap_cache = {}
                        total_visual = self.renderer.get_total_visual_lines(
                            cr, self.buf, ln_width, width
                        )
                        self.renderer.wrap_cache = saved_cache

                        if previous_estimated_total and previous_estimated_total > total_visual:
                            total_visual = previous_estimated_total

                        self.renderer.total_visual_lines_cache = total_visual

                    else:
                        buffer = visible_lines * 3
                        start_line = max(0, saved_cursor_line - buffer)
                        end_line = min(total_lines, saved_cursor_line + buffer)
                        for ln in range(start_line, end_line):
                            self.renderer.get_wrap_points_for_line(
                                cr, self.buf, ln, ln_width, width
                            )

                        total_visual = self.renderer.get_total_visual_lines(
                            cr, self.buf, ln_width, width
                        )

                # -------------------------------------------------------
                # PATCHED SECTION — accurate cursor anchoring after wrap
                # -------------------------------------------------------
                if width > 0 and height > 0 and total_lines > 0:
                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                    cr = cairo.Context(surface)
                    ln_width = self.renderer.calculate_line_number_width(cr, total_lines)

                    cursor_visual = self.renderer.logical_to_visual_line(
                        cr, self.buf, saved_cursor_line, saved_cursor_col,
                        ln_width, width
                    )

                    # Convert back to logical + visual offset
                    new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                        cr, self.buf, cursor_visual, ln_width, width
                    )

                    self.scroll_line = new_log
                    self.scroll_visual_offset = new_vis_off

                    # Sync vadj to cursor’s true visual line
                    self.vadj.handler_block_by_func(self.on_vadj_changed)
                    try:
                        upper = max(cursor_visual + 1, int(self.vadj.get_upper()))
                        self.vadj.set_upper(upper)
                        self.vadj.set_value(cursor_visual)
                    finally:
                        self.vadj.handler_unblock_by_func(self.on_vadj_changed)
                else:
                    # fallback (tiny or zero viewport) — keep original behavior
                    estimated_scroll = max(0, saved_cursor_line - visible_lines // 2)
                    self.scroll_line = estimated_scroll
                    self.scroll_visual_offset = 0
                # -------------------------------------------------------

            else:
                # Disabling wrap
                self.scroll_visual_offset = 0
                estimated_scroll = max(0, saved_cursor_line - visible_lines // 2)
                self.scroll_line = estimated_scroll

                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                self.renderer.scan_for_max_width(cr, self.buf)

            # Update scrollbar
            self.update_scrollbar()

            # Correction pass
            cursor_corrected = False
            if self.renderer.wrap_enabled and width > 0 and height > 0:
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                ln_width = self.renderer.calculate_line_number_width(cr, total_lines)

                cursor_visual = self.renderer.logical_to_visual_line(
                    cr, self.buf, saved_cursor_line, saved_cursor_col, ln_width, width
                )

                current_estimate = self.vadj.get_upper()

                if total_lines > 5000 and cursor_visual > current_estimate * 0.95:
                    if saved_cursor_line > 0:
                        actual_ratio = cursor_visual / saved_cursor_line
                        corrected_total = int(total_lines * actual_ratio)
                        corrected_total = int(corrected_total * 1.02) + 100

                        self.renderer.total_visual_lines_cache = corrected_total

                        self.vadj.handler_block_by_func(self.on_vadj_changed)
                        try:
                            self.vadj.set_upper(corrected_total)
                            max_scroll = max(0, corrected_total - visible_lines)
                            target_visual = max(0, cursor_visual - visible_lines // 2)
                            target_visual = min(target_visual, max_scroll)
                            self.vadj.set_value(target_visual)

                            new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                                cr, self.buf, target_visual, ln_width, width
                            )
                            self.scroll_line = new_log
                            self.scroll_visual_offset = new_vis_off

                            cursor_corrected = True
                        finally:
                            self.vadj.handler_unblock_by_func(self.on_vadj_changed)

            if not cursor_corrected:
                self.keep_cursor_visible()

            self.queue_draw()
            return True

        # ... rest of your key handling unchanged ...



        # Alt+Arrow keys for text movement
        if alt_pressed and name == "Left":
            self.buf.move_word_left_with_text()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif alt_pressed and name == "Right":
            self.buf.move_word_right_with_text()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif alt_pressed and name == "Up":
            self.buf.move_line_up_with_text()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif alt_pressed and name == "Down":
            self.buf.move_line_down_with_text()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if keyval == Gdk.KEY_Tab:
            # Check for Shift+Tab (Unindent)
            if (state & Gdk.ModifierType.SHIFT_MASK):
                self.buf.unindent_selection()
                self.queue_draw()
                return True
            
            # Check for Multi-line Indent
            if self.buf.selection.has_selection():
                start_line, _, end_line, _ = self.buf.selection.get_bounds()
                if start_line != end_line:
                    self.buf.indent_selection()
                    self.queue_draw()
                    return True
            
            # Normal Tab (Insert tabs or spaces)
            if getattr(self, "use_tabs", True):
                self.buf.insert_text("\t")
            else:
                tab_width = getattr(self.renderer, "tab_width", 4)
                self.buf.insert_text(" " * tab_width)
            self.queue_draw()
            return True

        if keyval == Gdk.KEY_ISO_Left_Tab:
            self.buf.unindent_selection()
            self.queue_draw()
            return True

        # Ctrl+A - Select All
        if ctrl_pressed and name == "a":
            self.buf.select_all()
            self.queue_draw()
            return True
        
        # Ctrl+C - Copy
        if ctrl_pressed and name == "c":
            self.copy_to_clipboard()
            return True
        
        # Ctrl+X - Cut
        if ctrl_pressed and name == "x":
            self.cut_to_clipboard()
            return True
        
        # Ctrl+V - Paste
        if ctrl_pressed and name == "v":
            self.paste_from_clipboard()
            return True
        
        # Insert key - Toggle overwrite mode
        if name == "Insert" and not ctrl_pressed and not shift_pressed:
            self.overwrite_mode = not self.overwrite_mode
            # Visual feedback could be added here (cursor shape change, status bar indicator, etc.)
            print(f"Overwrite mode: {'ON' if self.overwrite_mode else 'OFF'}")
            self.queue_draw()
            return True
        
        # Tab key - insert tab character
        if name == "Tab":
            self.buf.insert_text("\t")
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Editing keys
        if name == "BackSpace":
            if ctrl_pressed and shift_pressed:
                # Ctrl+Shift+Backspace: Delete to start of line
                self.buf.delete_to_line_start()
            elif ctrl_pressed:
                # Ctrl+Backspace: Delete word backward
                self.buf.delete_word_backward()
            else:
                # Normal backspace
                self.buf.backspace()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Delete":
            if ctrl_pressed and shift_pressed:
                # Ctrl+Shift+Delete: Delete to end of line
                self.buf.delete_to_line_end()
            elif ctrl_pressed:
                # Ctrl+Delete: Delete word forward
                self.buf.delete_word_forward()
            else:
                # Normal delete
                self.buf.delete_key()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Return":
            self.buf.insert_newline()
            
            # Auto-indentation
            if getattr(self, "auto_indent", True):
                current_line_idx = self.buf.cursor_line - 1 # Line we just left
                if current_line_idx >= 0:
                    line_text = self.buf.get_line(current_line_idx)
                    indent = ""
                    for char in line_text:
                        if char in (" ", "\t"):
                            indent += char
                        else:
                            break
                    
                    if indent:
                        self.buf.insert_text(indent)

            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Navigation with selection support
        if name == "Up":
            self.ctrl.move_up(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Down":
            self.ctrl.move_down(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Left":
            if ctrl_pressed:
                # Proper word navigation
                self.ctrl.move_word_left(extend_selection=shift_pressed)
            else:
                self.ctrl.move_left(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Right":
            if ctrl_pressed:
                # Proper word navigation
                self.ctrl.move_word_right(extend_selection=shift_pressed)
            else:
                self.ctrl.move_right(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Home":
            if ctrl_pressed:
                self.ctrl.move_document_start(extend_selection=shift_pressed)
            else:
                self.ctrl.move_home(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "End":
            if ctrl_pressed:
                self.ctrl.move_document_end(extend_selection=shift_pressed)
            else:
                self.ctrl.move_end(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Page_Up":
            # Move up by visible lines
            visible_lines = self.get_height() // self.renderer.line_h
            for _ in range(visible_lines):
                self.ctrl.move_up(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Page_Down":
            # Move down by visible lines
            visible_lines = self.get_height() // self.renderer.line_h
            for _ in range(visible_lines):
                self.ctrl.move_down(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        return False



# ============================================================
#   INSTALLATION / INTEGRATION
# ============================================================

def has_keyboard_support():
    """Check if keyboard feature dependencies are available"""
    return KEYBOARD_FEATURES_AVAILABLE


def install_keyboard_feature(view_class):
    """Validate that view class has required interface"""
    required_methods = ['keep_cursor_visible', 'update_im_cursor_location', 'queue_draw']
    for method in required_methods:
        if not hasattr(view_class, method):
            print(f"Warning: View missing method: {method}")
            return False
    return True


# Export public API
__all__ = [
    'KeyboardHandler',
    'install_keyboard_feature',
    'has_keyboard_support',
    'KEYBOARD_FEATURES_AVAILABLE'
]
