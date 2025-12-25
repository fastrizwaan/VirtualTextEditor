#!/usr/bin/env python3
"""
Self-contained Clipboard Feature Module

Provides clipboard operations (copy/cut/paste) for text editing.
Following the pattern of find_feature.py and undo_redo_feature.py.

Usage:
    from clipboard_feature import ClipboardHandler
    
    handler = ClipboardHandler(view, buffer)
    handler.copy_to_clipboard()
    handler.paste_from_clipboard()
"""

try:
    import gi
    gi.require_version('Gdk', '4.0')
    gi.require_version('GLib', '2.0')
    from gi.repository import Gdk, GLib
    CLIPBOARD_FEATURES_AVAILABLE = True
except ImportError:
    CLIPBOARD_FEATURES_AVAILABLE = False
    Gdk = None
    GLib = None


class ClipboardHandler:
    """Handles clipboard operations"""
    
    def __init__(self, view, buf):
        """
        Initialize clipboard handler
        
        Args:
            view: The view object (needs get_clipboard, show_busy, hide_busy, queue_draw)
            buf: The buffer object (needs get_selected_text, delete_selection, insert_text)
        """
        self.view = view
        self.buf = buf
    
    def copy_to_clipboard(self):
        """Copy selected text to clipboard with progress indicator"""
        self.view.show_busy("Copying...")
        
        # Defer execution to allow UI to render the busy overlay
        def _do_copy():
            try:
                text = self.buf.get_selected_text()
                if text:
                    clipboard = self.view.get_clipboard()
                    clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
            finally:
                self.view.hide_busy()
            return False
            
        GLib.timeout_add(20, _do_copy)

    def cut_to_clipboard(self):
        """Cut selected text to clipboard with progress indicator"""
        self.view.show_busy("Cutting...")
        
        # Defer execution
        def _do_cut():
            try:
                text = self.buf.get_selected_text()
                if text:
                    clipboard = self.view.get_clipboard()
                    clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
                    # Pass the text we just fetched to delete_selection to avoid re-fetching it
                    self.buf.delete_selection(provided_text=text)
                    self.view.queue_draw()
            finally:
                self.view.hide_busy()
            return False
            
        GLib.timeout_add(20, _do_cut)

    def paste_from_clipboard(self):
        """Paste text from clipboard with better error handling and progress"""
        clipboard = self.view.get_clipboard()
        view = self.view
        buf = self.buf
        
        def paste_ready(clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
                if text:
                    view.show_busy("Pasting...")
                    
                    # Defer insert to allow UI update
                    def _do_paste():
                        try:
                            buf.insert_text(text)
                            
                            # After paste, clear wrap cache and recalculate everything
                            if view.renderer.wrap_enabled:
                                view.renderer.wrap_cache.clear()
                                view.renderer.total_visual_lines_cache = None
                                view.renderer.estimated_total_cache = None
                                view.renderer.visual_line_map = []
                                view.renderer.edits_since_cache_invalidation = 0
                        finally:
                            view.hide_busy()
                            view.queue_draw()
                        return False
                    
                    GLib.timeout_add(20, _do_paste)
                    
            except Exception as e:
                # Handle finish error
                error_msg = str(e)
                if "No compatible transfer format" not in error_msg:
                    print(f"Paste error: {e}")
                view.clipboard_handler.try_paste_fallback()

        clipboard.read_text_async(None, paste_ready)

    def try_paste_fallback(self):
        """Fallback method to try getting clipboard text"""
        try:
            clipboard = self.view.get_clipboard()
            view = self.view
            buf = self.buf
            
            # Try to get formats available
            formats = clipboard.get_formats()
            
            # Check if text is available in any format
            if formats.contain_mime_type("text/plain"):
                # Try reading as plain text with UTF-8 encoding
                def read_ready(clipboard, result):
                    try:
                        success, data = clipboard.read_finish(result)
                        if success and data:
                            # Try to decode as UTF-8
                            text = data.decode('utf-8', errors='ignore')
                            if text:
                                buf.insert_text(text)
                                
                                # After paste, clear wrap cache and recalculate everything
                                if view.renderer.wrap_enabled:
                                    view.renderer.wrap_cache.clear()
                                    view.renderer.total_visual_lines_cache = None
                                    view.renderer.estimated_total_cache = None
                                    view.renderer.visual_line_map = []
                                    view.renderer.edits_since_cache_invalidation = 0
                                
                                view.keep_cursor_visible()
                                view.update_scrollbar()  # Update scrollbar range after paste
                                view.update_im_cursor_location()
                                view.queue_draw()
                    except Exception as e:
                        # Silently fail - clipboard probably contains non-text data
                        pass
                
                clipboard.read_async(["text/plain"], 0, None, read_ready)
        except Exception as e:
            # Silently fail - this is just a fallback attempt
            pass

    def install_keys(self):
        key = Gtk.EventControllerKey()


# ============================================================
#   INSTALLATION / INTEGRATION
# ============================================================

def has_clipboard_support():
    """Check if clipboard feature dependencies are available"""
    return CLIPBOARD_FEATURES_AVAILABLE


def install_clipboard_feature(view_class):
    """Validate that view class has required interface"""
    required_methods = ['get_clipboard', 'show_busy', 'hide_busy']
    for method in required_methods:
        if not hasattr(view_class, method):
            print(f"Warning: View missing method: {method}")
            return False
    return True


# Export public API
__all__ = [
    'ClipboardHandler',
    'install_clipboard_feature',
    'has_clipboard_support',
    'CLIPBOARD_FEATURES_AVAILABLE'
]
