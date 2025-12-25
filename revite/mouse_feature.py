#!/usr/bin/env python3
"""
Self-contained Mouse Feature Module

Provides mouse/drag event handling for text editing.
Following the pattern of find_feature.py and undo_redo_feature.py.

Usage:
    from mouse_feature import MouseHandler
    
    handler = MouseHandler(view, buffer, input_controller)
    handler.on_click_pressed(gesture, n_press, x, y)
"""

try:
    import gi
    gi.require_version('Gdk', '4.0')
    from gi.repository import Gdk
    import cairo
    MOUSE_FEATURES_AVAILABLE = True
except ImportError:
    MOUSE_FEATURES_AVAILABLE = False
    Gdk = None
    cairo = None


class MouseHandler:
    """Handles mouse and drag events"""
    
    def __init__(self, view, buf, input_controller):
        """
        Initialize mouse handler
        
        Args:
            view: The view object (needs renderer, scrolling, coordinate conversion)
            buf: The buffer object
            input_controller: The InputController for click/drag operations
        """
        self.view = view
        self.buf = buf
        self.ctrl = input_controller
    
    def on_middle_click(self, gesture, n_press, x, y):
        """Paste from primary clipboard on middle-click"""
        self.grab_focus()
        
        # Always use accurate xy_to_line_col
        ln, col = self.xy_to_line_col(x, y)
        
        # Move cursor to click position
        self.buf.set_cursor(ln, col)
        
        # Paste from PRIMARY clipboard (not CLIPBOARD)
        display = self.get_display()
        clipboard = display.get_primary_clipboard()
        clipboard.read_text_async(None, self.on_primary_paste_ready)
        
        self.queue_draw()

    def on_primary_paste_ready(self, clipboard, result):
        """Callback when primary clipboard text is ready"""
        try:
            text = clipboard.read_text_finish(result)
            if text:
                # Delete selection if any
                if self.buf.selection.has_selection():
                    self.buf.delete_selection()
                
                # Insert text at cursor
                self.buf.insert_text(text)
                
                # After paste, clear wrap cache and recalculate everything
                if self.renderer.wrap_enabled:
                    self.renderer.wrap_cache.clear()
                    self.renderer.total_visual_lines_cache = None
                    self.renderer.estimated_total_cache = None
                    self.renderer.visual_line_map = []
                    self.renderer.edits_since_cache_invalidation = 0
                
                self.keep_cursor_visible()
                self.update_scrollbar()  # Update scrollbar range after paste
                self.update_im_cursor_location()
                self.queue_draw()
        except Exception as e:
            print(f"Primary paste error: {e}")

    def on_right_click(self, gesture, n_press, x, y):
        """Show context menu on right-click"""
        self.grab_focus()
        
        # Create popover menu
        menu = Gtk.PopoverMenu()
        menu.set_parent(self)
        menu.set_has_arrow(False)
        
        # Create menu model
        menu_model = Gio.Menu()
        
        has_selection = self.buf.selection.has_selection()
        
        if has_selection:
            # Menu items for when there's a selection
            menu_model.append("Cut", "view.cut")
            menu_model.append("Copy", "view.copy")
            menu_model.append("Paste", "view.paste")
            menu_model.append("Delete", "view.delete")
        else:
            # Menu items for when there's no selection
            menu_model.append("Paste", "view.paste")
        
        # Always show these
        menu_model.append("Select All", "view.select-all")
        # Undo/Redo commented out until implemented
        menu_model.append("Undo", "view.undo")
        menu_model.append("Redo", "view.redo")
        
        menu.set_menu_model(menu_model)
        
        # Create action group if not exists
        if not hasattr(self, 'action_group'):
            self.action_group = Gio.SimpleActionGroup()
            self.insert_action_group("view", self.action_group)
            
            # Create actions using a loop
            actions = [
                ("cut", self.cut_to_clipboard),
                ("copy", self.copy_to_clipboard),
                ("paste", self.paste_from_clipboard),
                ("delete", self.on_delete_action),
                ("select-all", lambda: self.buf.select_all()),
                ("undo", self.on_undo_action),
                ("redo", self.on_redo_action),
            ]
            
            for action_name, callback in actions:
                action = Gio.SimpleAction.new(action_name, None)
                action.connect("activate", lambda a, p, cb=callback: cb())
                self.action_group.add_action(action)
        
        # Position the menu at the click location with slight offset
        rect = Gdk.Rectangle()
        rect.x = int(x) + 60
        rect.y = int(y) - 1
        rect.width = 1
        rect.height = 1
        menu.set_pointing_to(rect)
        
        menu.popup()

    def on_delete_action(self):
        """Delete selected text"""
        if self.buf.selection.has_selection():
            self.buf.delete_selection()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()

    def on_undo_action(self):
        """Placeholder for undo - to be implemented"""
        print("Undo - to be implemented")

    def on_redo_action(self):
        """Placeholder for redo - to be implemented"""
        print("Redo - to be implemented")

    def find_word_boundaries(self, line, col):
        """Find word boundaries at the given position. Words include alphanumeric and underscore."""
        import unicodedata
        
        if not line:
            return 0, 0
        
        # Check if character is a word character (letter, number, underscore, or combining mark)
        def is_word_char(ch):
            if ch == '_':
                return True
            cat = unicodedata.category(ch)
            # Letter categories: Lu, Ll, Lt, Lm, Lo
            # Number categories: Nd, Nl, No
            # Mark categories: Mn, Mc, Me (for combining characters like Devanagari vowel signs)
            return cat[0] in ('L', 'N', 'M')
        
        # If clicking beyond line or on whitespace/punctuation, select just that position
        if col >= len(line) or not is_word_char(line[col]):
            return col, min(col + 1, len(line))
        
        # Find start of word
        start = col
        while start > 0 and is_word_char(line[start - 1]):
            start -= 1
        
        # Find end of word
        end = col
        while end < len(line) and is_word_char(line[end]):
            end += 1
        
        return start, end


    def on_release(self, g, n, x, y):
        """Handle mouse button release"""
        self.stop_autoscroll()  # Stop auto-scroll on release
        self.ctrl.end_drag()




    def xy_to_line_col(self, x, y):
        """Convert pixel coordinates to logical line and column."""
        # We need a dummy surface for renderer methods that expect 'cr'
        # even though we use create_hit_test_layout for metrics.
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        # Use cached line number width if available to ensure consistency with rendering
        if hasattr(self.renderer, 'last_ln_width') and self.renderer.last_ln_width is not None and self.renderer.last_ln_width > 0:
            ln_width = self.renderer.last_ln_width
        else:
            # Fallback: calculate using widget context (accurate)
            layout = self.create_hit_test_layout(str(self.buf.total()))
            w, _ = layout.get_pixel_size()
            ln_width = w + 15
        viewport_width = self.get_width()

        # ------------------------------------------------------------
        # NORMAL PATH (wrap disabled)
        # ------------------------------------------------------------
        if not self.renderer.wrap_enabled:
            vis_line = self.scroll_line + int(y // self.renderer.line_h)
            ln = max(0, min(vis_line, self.buf.total() - 1))
            
            text = self.buf.get_line(ln)
            is_rtl = detect_rtl_line(text)

            text_w = self.renderer.get_text_width(cr, text)
            base_x = self.renderer.calculate_text_base_x(
                is_rtl, text_w, viewport_width, ln_width, self.scroll_x
            )

            col_pixels = x - base_x
            col = self.pixel_to_column(cr, text, col_pixels)
            col = max(0, min(col, len(text)))
            return ln, col

        # ------------------------------------------------------------
        # WRAP-AWARE PATH: Accurate iteration limited to viewport
        # ------------------------------------------------------------
        current_y = 0
        ln = self.scroll_line
        total_lines = self.buf.total()

        viewport_height = self.get_height()
        max_y_to_check = viewport_height + self.renderer.line_h * 2
        
        # Safety counter to prevent infinite loops
        max_iterations = 200
        iteration_count = 0

        while ln < total_lines and iteration_count < max_iterations:
            iteration_count += 1
            
            wrap_points = self.renderer.get_wrap_points_for_line(
                cr, self.buf, ln, ln_width, viewport_width
            )
            num_visual = len(wrap_points)

            start_vis_idx = 0
            if ln == self.scroll_line:
                start_vis_idx = self.scroll_visual_offset
                if start_vis_idx >= num_visual:
                    start_vis_idx = max(0, num_visual - 1)

            for vis_idx in range(start_vis_idx, num_visual):
                if current_y <= y < current_y + self.renderer.line_h:
                    # Found the visual line that was clicked
                    col_start, col_end = wrap_points[vis_idx]

                    full_text = self.buf.get_line(ln)
                    if col_end > col_start:
                        text_segment = full_text[col_start:col_end]
                    else:
                        text_segment = full_text[col_start:] if col_start < len(full_text) else ""

                    is_rtl = detect_rtl_line(text_segment)
                    text_w = self.renderer.get_text_width(cr, text_segment)

                    base_x = self.renderer.calculate_text_base_x(
                        is_rtl, text_w, viewport_width, ln_width, self.scroll_x
                    )

                    col_pixels = x - base_x
                    col_in_segment = self.pixel_to_column(cr, text_segment, col_pixels)
                    col_in_segment = max(0, min(col_in_segment, len(text_segment)))

                    col = col_start + col_in_segment
                    return ln, col

                current_y += self.renderer.line_h
                if current_y > max_y_to_check:
                    break

            if current_y > max_y_to_check:
                break

            ln += 1

        # Fallback: click was beyond visible area
        last_ln = max(0, total_lines - 1)
        last_line_text = self.buf.get_line(last_ln)
        return last_ln, len(last_line_text)



    def start_autoscroll(self):
        """Start the auto-scroll timer if not already running"""
        if self.autoscroll_timer_id is None:
            # Call autoscroll_tick every 50ms (20 times per second)
            self.autoscroll_timer_id = GLib.timeout_add(50, self.autoscroll_tick)
    
    def stop_autoscroll(self):
        """Stop the auto-scroll timer"""
        if self.autoscroll_timer_id is not None:
            GLib.source_remove(self.autoscroll_timer_id)
            self.autoscroll_timer_id = None
    
    def autoscroll_tick(self):
        """Called periodically during drag to perform auto-scrolling"""
        if not self.ctrl.dragging and not self.drag_and_drop_mode:
            # No longer dragging, stop the timer
            self.stop_autoscroll()
            return False
        
        viewport_height = self.get_height()
        viewport_width = self.get_width()
        
        # Define edge zones (pixels from edge where auto-scroll activates)
        edge_size = 30
        
        # Calculate scroll amounts based on how close to edge
        scroll_amount = 0
        hscroll_amount = 0
        
        # Vertical scrolling
        if self.last_drag_y < edge_size:
            # Near top edge - scroll up
            # Speed increases closer to edge
            scroll_amount = -max(1, int((edge_size - self.last_drag_y) / 10) + 1)
        elif self.last_drag_y > viewport_height - edge_size:
            # Near bottom edge - scroll down
            scroll_amount = max(1, int((self.last_drag_y - (viewport_height - edge_size)) / 10) + 1)
        
        # Horizontal scrolling (only when wrap is disabled)
        if not self.renderer.wrap_enabled:
            ln_width = 50  # Approximate line number width
            if self.last_drag_x < ln_width + edge_size:
                # Near left edge - scroll left
                hscroll_amount = -max(5, int((ln_width + edge_size - self.last_drag_x) / 5) + 5)
            elif self.last_drag_x > viewport_width - edge_size:
                # Near right edge - scroll right
                hscroll_amount = max(5, int((self.last_drag_x - (viewport_width - edge_size)) / 5) + 5)
        
        # Perform scrolling
        did_scroll = False
        
        if scroll_amount != 0:
            total_lines = self.buf.total()
            if total_lines == 0:
                return True
            
            visible = max(1, viewport_height // self.renderer.line_h)
            
            if self.renderer.wrap_enabled:
                # Word wrap mode: scroll by visual lines
                
                # Use cached line number width if available
                if hasattr(self.renderer, 'last_ln_width') and self.renderer.last_ln_width is not None and self.renderer.last_ln_width > 0:
                    ln_width = self.renderer.last_ln_width
                else:
                    # Fallback: calculate using widget context
                    layout = self.create_hit_test_layout(str(total_lines))
                    w, _ = layout.get_pixel_size()
                    ln_width = w + 15
                
                # Prepare dummy cr for logical_to_visual_line
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                cr = cairo.Context(surface)
                
                # Calculate current visual line
                current_visual = self.renderer.logical_to_visual_line(
                    cr, self.buf, self.scroll_line, 0, ln_width, viewport_width
                )
                current_visual += self.scroll_visual_offset
                
                # Calculate total visual lines for bounds checking
                total_visual = self.renderer.get_total_visual_lines(cr, self.buf, ln_width, viewport_width)
                max_scroll_visual = max(0, total_visual - visible)
                
                # Apply scroll
                new_visual = current_visual + scroll_amount
                new_visual = max(0, min(new_visual, max_scroll_visual))
                
                if new_visual != current_visual:
                    # Convert back to logical line + visual offset
                    new_log, new_vis_off, _, _ = self.renderer.visual_to_logical_line(
                        cr, self.buf, new_visual, ln_width, viewport_width
                    )
                    
                    self.scroll_line = new_log
                    self.scroll_visual_offset = new_vis_off
                    self.vadj.set_value(new_visual)
                    did_scroll = True
            else:
                # Non-wrap mode: scroll by logical lines
                new_scroll = self.scroll_line + scroll_amount
                max_scroll = max(0, total_lines - visible)
                new_scroll = max(0, min(new_scroll, max_scroll))
                
                if new_scroll != self.scroll_line:
                    self.scroll_line = new_scroll
                    self.scroll_visual_offset = 0
                    self.vadj.set_value(self.scroll_line)
                    did_scroll = True
        
        if hscroll_amount != 0 and not self.renderer.wrap_enabled:
            new_scroll_x = self.scroll_x + hscroll_amount
            max_hscroll = max(0, self.renderer.max_line_width - viewport_width)
            new_scroll_x = max(0, min(new_scroll_x, max_hscroll))
            
            if new_scroll_x != self.scroll_x:
                self.scroll_x = new_scroll_x
                self.hadj.set_value(self.scroll_x)
                did_scroll = True
        
        # Update selection after scrolling
        if did_scroll:
            # Get the line/col at current drag position
            ln, col = self.xy_to_line_col(self.last_drag_x, self.last_drag_y)
            
            # Update drag selection to follow the cursor
            if self.drag_and_drop_mode:
                # In drag-and-drop mode, just update drop position
                self.drop_position_line = ln
                self.drop_position_col = col
            elif self.word_selection_mode:
                # Word selection mode - extend by words
                line_text = self.buf.get_line(ln)
                if line_text and 0 <= col <= len(line_text):
                    start_col, end_col = self.find_word_boundaries(line_text, min(col, len(line_text) - 1))
                    
                    # Use anchor word for direction
                    is_forward = False
                    if ln > self.anchor_word_start_line:
                        is_forward = True
                    elif ln == self.anchor_word_start_line and col >= self.anchor_word_start_col:
                        is_forward = True
                    
                    if is_forward:
                        self.buf.selection.set_start(self.anchor_word_start_line, self.anchor_word_start_col)
                        self.ctrl.update_drag(ln, end_col)
                    else:
                        self.buf.selection.set_start(self.anchor_word_end_line, self.anchor_word_end_col)
                        self.ctrl.update_drag(ln, start_col)
                else:
                    self.ctrl.update_drag(ln, col)
            else:
                # Normal character selection
                self.ctrl.update_drag(ln, col)
            
            self.queue_draw()
        
        # Keep timer running
        return True


    def show_busy(self, message="Processing..."):
        """Show the busy overlay with a message."""
        if self._busy_overlay:
            self._busy_label.set_text(message)
            self._busy_spinner.start()
            self._busy_overlay.set_visible(True)
            # Force UI update if possible, though usually handled by loop return
            
    def hide_busy(self):
        """Hide the busy overlay."""
        if self._busy_overlay:
            self._busy_spinner.stop()
            self._busy_overlay.set_visible(False)



def has_mouse_support():
    """Check if mouse feature dependencies are available"""
    return MOUSE_FEATURES_AVAILABLE


def install_mouse_feature(view_class):
    """Validate that view class has required interface"""
    required_methods = ['pixel_to_line_col', 'queue_draw']
    for method in required_methods:
        if not hasattr(view_class, method):
            print(f"Warning: View missing method: {method}")
            return False
    return True


# Export public API
__all__ = [
    'MouseHandler',
    'install_mouse_feature',
    'has_mouse_support',
    'MOUSE_FEATURES_AVAILABLE'
]
