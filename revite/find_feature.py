#!/usr/bin/env python3
"""
Self-contained Find/Replace Feature Module

This module provides a complete find/replace functionality that can be integrated
into any text editor application. It's designed to be:
- Self-contained: All dependencies are checked and handled gracefully
- Reusable: Can be used in multiple projects
- Optional: The main application works without this module

Usage:
    from find_feature import install_find_feature, FindReplaceBar
    
    # Install search capabilities into your buffer and view classes
    if install_find_feature(MyBuffer, MyView, MyEditorPage):
        # Feature installed successfully
        pass
"""

import re
from typing import Protocol, Tuple, List, Optional, Callable, Any

# Check for GTK4 availability
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gtk, Gdk, GLib
    GTK_AVAILABLE = True
except (ImportError, ValueError) as e:
    GTK_AVAILABLE = False
    print(f"Warning: GTK4 not available - find feature disabled: {e}")


# ============================================================
#   PROTOCOL DEFINITIONS
# ============================================================

class SearchableBuffer(Protocol):
    """Protocol defining the interface required for search operations"""
    
    def total(self) -> int:
        """Return total number of lines in buffer"""
        ...
    
    def get_line(self, line_num: int) -> str:
        """Get text content of a specific line"""
        ...
    
    def begin_action(self) -> None:
        """Begin a composite action for undo grouping"""
        ...
    
    def end_action(self) -> None:
        """End a composite action"""
        ...


class SearchableView(Protocol):
    """Protocol defining the interface required for search UI"""
    
    def grab_focus(self) -> None:
        """Give focus to the view"""
        ...
    
    def queue_draw(self) -> None:
        """Request a redraw of the view"""
        ...
    
    def get_width(self) -> int:
        """Get view width in pixels"""
        ...
    
    def get_height(self) -> int:
        """Get view height in pixels"""
        ...


# ============================================================
#   SEARCH ENGINE
# ============================================================

class SearchEngine:
    """Handles all search operations with support for regex, case-sensitivity, and whole-word matching"""
    
    @staticmethod
    def search(buffer: SearchableBuffer, query: str, case_sensitive: bool = False, 
               is_regex: bool = False, max_matches: int = 0) -> List[Tuple[int, int, int, int, str]]:
        """
        Search in buffer for query.
        
        Args:
            buffer: Buffer to search in
            query: Search query
            case_sensitive: Whether search is case-sensitive
            is_regex: Whether query is a regex pattern
            max_matches: Maximum matches to find (0 = unlimited)
            
        Returns:
            List of matches as tuples: (start_line, start_col, end_line, end_col, matched_text)
        """
        if not query:
            return []
            
        matches = []
        flags = 0 if case_sensitive else re.IGNORECASE
        
        pattern = None
        if is_regex:
            try:
                pattern = re.compile(query, flags)
            except re.error:
                print(f"Invalid regex: {query}")
                return []
        
        # Prepare query for non-regex search
        query_text = query if case_sensitive else query.lower()
        if not query_text:
            return []
            
        total_lines = buffer.total()
        
        for i in range(total_lines):
            line_text = buffer.get_line(i)
            if not line_text:
                continue
                
            line_matches = []
            
            if is_regex:
                for m in pattern.finditer(line_text):
                    start_col = m.start()
                    end_col = m.end()
                    line_matches.append((i, start_col, i, end_col, m.group()))
            else:
                start = 0
                search_text = line_text if case_sensitive else line_text.lower()

                while True:
                    idx = search_text.find(query_text, start)
                    if idx == -1:
                        break
                    
                    end_idx = idx + len(query_text)
                    line_matches.append((i, idx, i, end_idx, line_text[idx:end_idx]))
                    start = end_idx
                    
            matches.extend(line_matches)
            if max_matches > 0 and len(matches) >= max_matches:
                matches = matches[:max_matches]
                break
            
        return matches

    @staticmethod
    def search_async(buffer: SearchableBuffer, query: str, case_sensitive: bool, is_regex: bool, 
                     max_matches: int, on_progress: Optional[Callable], on_complete: Callable,
                     chunk_size: int = 10000) -> Callable:
        """
        Asynchronous search that processes lines in chunks to keep UI responsive.
        
        Args:
            buffer: Buffer to search in
            query: Search query
            case_sensitive: Case sensitive flag
            is_regex: Use regex flag
            max_matches: Maximum matches to find (0=unlimited)
            on_progress: Callback(matches_so_far, lines_searched, total_lines)
            on_complete: Callback(final_matches)
            chunk_size: Number of lines to process per idle callback
        
        Returns:
            A cancel function that can be called to abort the search
        """
        if not GTK_AVAILABLE:
            on_complete([])
            return lambda: None
            
        if not query:
            on_complete([])
            return lambda: None
            
        flags = 0 if case_sensitive else re.IGNORECASE
        
        pattern = None
        if is_regex:
            try:
                pattern = re.compile(query, flags)
            except re.error:
                print(f"Invalid regex: {query}")
                on_complete([])
                return lambda: None
        
        query_text = query if case_sensitive else query.lower()
        if not query_text and not is_regex:
            on_complete([])
            return lambda: None
            
        total_lines = buffer.total()
        
        # State for the async search
        state = {
            'matches': [],
            'current_line': 0,
            'cancelled': False,
            'idle_id': None
        }
        
        def search_chunk():
            if state['cancelled']:
                return False  # Stop idle callback
                
            end_line = min(state['current_line'] + chunk_size, total_lines)
            
            for i in range(state['current_line'], end_line):
                line_text = buffer.get_line(i)
                if not line_text:
                    continue
                    
                line_matches = []
                
                if is_regex:
                    for m in pattern.finditer(line_text):
                        start_col = m.start()
                        end_col = m.end()
                        line_matches.append((i, start_col, i, end_col, m.group()))
                else:
                    start = 0
                    search_text = line_text if case_sensitive else line_text.lower()

                    while True:
                        idx = search_text.find(query_text, start)
                        if idx == -1:
                            break
                        
                        end_idx = idx + len(query_text)
                        line_matches.append((i, idx, i, end_idx, line_text[idx:end_idx]))
                        start = end_idx
                        
                state['matches'].extend(line_matches)
                
                # Check if we've hit the match limit
                if max_matches > 0 and len(state['matches']) >= max_matches:
                    state['matches'] = state['matches'][:max_matches]
                    on_complete(state['matches'])
                    return False  # Stop
            
            state['current_line'] = end_line
            
            # Report progress
            if on_progress:
                on_progress(state['matches'], state['current_line'], total_lines)
            
            # Check if we're done
            if state['current_line'] >= total_lines:
                on_complete(state['matches'])
                return False  # Stop
                
            return True  # Continue with next chunk
        
        # Start the async search
        state['idle_id'] = GLib.idle_add(search_chunk)
        
        def cancel():
            state['cancelled'] = True
            if state['idle_id']:
                GLib.source_remove(state['idle_id'])
                state['idle_id'] = None
        
        return cancel

    @staticmethod
    def search_viewport(buffer: SearchableBuffer, query: str, case_sensitive: bool, is_regex: bool,
                       start_line: int, end_line: int, max_matches: int = 500) -> List[Tuple[int, int, int, int, str]]:
        """
        Search only within a specific line range (for viewport-based searching).
        
        Args:
            buffer: Buffer to search in
            query: Search query
            case_sensitive: Case sensitive flag
            is_regex: Use regex flag
            start_line: First line to search (inclusive)
            end_line: Last line to search (inclusive)
            max_matches: Maximum matches to find
        
        Returns:
            List of matches within the specified range
        """
        if not query:
            return []
            
        matches = []
        flags = 0 if case_sensitive else re.IGNORECASE
        
        pattern = None
        if is_regex:
            try:
                pattern = re.compile(query, flags)
            except re.error:
                return []
        
        query_text = query if case_sensitive else query.lower()
        if not query_text and not is_regex:
            return []
            
        total_lines = buffer.total()
        start_line = max(0, start_line)
        end_line = min(total_lines - 1, end_line)
        
        for i in range(start_line, end_line + 1):
            line_text = buffer.get_line(i)
            if not line_text:
                continue
                
            if is_regex:
                for m in pattern.finditer(line_text):
                    matches.append((i, m.start(), i, m.end(), m.group()))
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches
            else:
                start = 0
                search_text = line_text if case_sensitive else line_text.lower()

                while True:
                    idx = search_text.find(query_text, start)
                    if idx == -1:
                        break
                    
                    end_idx = idx + len(query_text)
                    matches.append((i, idx, i, end_idx, line_text[idx:end_idx]))
                    start = end_idx
                    
                    if max_matches > 0 and len(matches) >= max_matches:
                        return matches
        
        return matches


# ============================================================
#   FIND/REPLACE BAR WIDGET (GTK4)
# ============================================================

if GTK_AVAILABLE:
    class FindReplaceBar(Gtk.Box):
        """GTK4 widget providing find/replace UI with all features"""
        
        def __init__(self, editor_page):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            self.editor = editor_page
            self.add_css_class("find-bar")
            self.set_visible(False)
            self._search_timeout_id = None
            self._scroll_refresh_timeout = None
            
            # Connect scroll callback for viewport-based search refresh
            if hasattr(self.editor.view, 'on_scroll_callback'):
                self.editor.view.on_scroll_callback = self._on_editor_scrolled
            
            # --- Top Row: Find ---
            find_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            find_box.set_margin_top(6)
            find_box.set_margin_bottom(6)
            find_box.set_margin_start(12)
            find_box.set_margin_end(12)
            
            # Find Entry Overlay logic
            self.find_overlay = Gtk.Overlay()
            self.find_entry = Gtk.SearchEntry()
            self.find_entry.set_hexpand(True)
            self.find_entry.set_placeholder_text("Find")
            self.find_entry.connect("search-changed", self.on_search_changed)
            self.find_entry.connect("activate", self.on_find_next)
            
            self.find_overlay.set_child(self.find_entry)
            
            # Matches Label (x of y)
            self.matches_label = Gtk.Label(label="")
            self.matches_label.add_css_class("dim-label")
            self.matches_label.add_css_class("caption")
            self.matches_label.set_margin_end(30)
            self.matches_label.set_halign(Gtk.Align.END)
            self.matches_label.set_valign(Gtk.Align.CENTER)
            self.matches_label.set_visible(False)
            self.matches_label.set_can_target(False)  # Make it click-through
            
            self.find_overlay.add_overlay(self.matches_label)
            
            # Capture Esc to close
            key_ctrl = Gtk.EventControllerKey()
            key_ctrl.connect("key-pressed", self.on_key_pressed)
            self.find_entry.add_controller(key_ctrl)
            
            find_box.append(self.find_overlay)
            
            # Navigation Box (linked)
            nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            nav_box.add_css_class("linked")
            
            self.prev_btn = Gtk.Button(icon_name="go-up-symbolic")
            self.prev_btn.set_tooltip_text("Previous Match (Shift+Enter)")
            self.prev_btn.connect("clicked", self.on_find_prev)
            nav_box.append(self.prev_btn)
            
            self.next_btn = Gtk.Button(icon_name="go-down-symbolic")
            self.next_btn.set_tooltip_text("Next Match (Enter)")
            self.next_btn.connect("clicked", self.on_find_next)
            nav_box.append(self.next_btn)
            
            find_box.append(nav_box)

            # Toggle Replace Mode Button (Icon)
            self.reveal_replace_btn = Gtk.Button()
            self.reveal_replace_btn.set_icon_name("edit-find-replace-symbolic")
            self.reveal_replace_btn.add_css_class("flat")
            self.reveal_replace_btn.connect("clicked", self.toggle_replace_mode)
            self.reveal_replace_btn.set_tooltip_text("Toggle Replace")
            find_box.append(self.reveal_replace_btn)

            # Search Options (Cog Wheel)
            self.options_btn = Gtk.MenuButton()
            self.options_btn.set_icon_name("system-run-symbolic")
            self.options_btn.set_tooltip_text("Search Options")
            self.options_btn.add_css_class("flat")
            
            # Create Popover Content
            popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            popover_box.set_margin_top(12)
            popover_box.set_margin_bottom(12)
            popover_box.set_margin_start(12)
            popover_box.set_margin_end(12)
            
            # Regex Option
            self.regex_check = Gtk.CheckButton(label="Regular Expressions")
            self.regex_check.connect("toggled", self.on_search_changed)
            popover_box.append(self.regex_check)
            
            # Case Option
            self.case_check = Gtk.CheckButton(label="Case Sensitive")
            self.case_check.connect("toggled", self.on_search_changed)
            popover_box.append(self.case_check)
            
            # Whole Word Option
            self.whole_word_check = Gtk.CheckButton(label="Match Whole Word Only")
            self.whole_word_check.connect("toggled", self.on_search_changed)
            popover_box.append(self.whole_word_check)
            
            self.options_popover = Gtk.Popover()
            self.options_popover.set_child(popover_box)
            self.options_btn.set_popover(self.options_popover)
            
            find_box.append(self.options_btn)
            
            # Close Button
            close_btn = Gtk.Button(icon_name="window-close-symbolic")
            close_btn.add_css_class("flat")
            close_btn.set_tooltip_text("Close Find Bar (Esc)")
            close_btn.connect("clicked", self.close)
            find_box.append(close_btn)
            
            self.append(find_box)
            
            # --- Bottom Row: Replace (Hidden by default) ---
            self.replace_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.replace_box.set_margin_bottom(6)
            self.replace_box.set_margin_start(12)
            self.replace_box.set_margin_end(12)
            self.replace_box.set_visible(False)
            
            self.replace_entry = Gtk.Entry()
            self.replace_entry.set_hexpand(True)
            self.replace_entry.set_placeholder_text("Replace")
            self.replace_entry.connect("activate", self.on_replace)
            self.replace_entry.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "edit-find-replace-symbolic")
            
            # New controller for replace entry
            replace_key_ctrl = Gtk.EventControllerKey()
            replace_key_ctrl.connect("key-pressed", self.on_key_pressed)
            self.replace_entry.add_controller(replace_key_ctrl)
            
            self.replace_box.append(self.replace_entry)
            
            # Action Buttons
            self.replace_btn = Gtk.Button(label="Replace")
            self.replace_btn.connect("clicked", self.on_replace)
            self.replace_box.append(self.replace_btn)
            
            self.replace_all_btn = Gtk.Button(label="Replace All")
            self.replace_all_btn.connect("clicked", self.on_replace_all)
            self.replace_box.append(self.replace_all_btn)
            
            self.append(self.replace_box)

        def toggle_replace_mode(self, btn):
            vis = not self.replace_box.get_visible()
            self.replace_box.set_visible(vis)
            
            if vis:
                self.replace_entry.grab_focus()
            else:
                self.find_entry.grab_focus()

        def show_search(self):
            self.set_visible(True)
            self.replace_box.set_visible(False)
            self.find_entry.grab_focus()
            # Select all text in find entry
            self.find_entry.select_region(0, -1)
            
        def show_replace(self):
            self.set_visible(True)
            self.replace_box.set_visible(True)
            self.find_entry.grab_focus()
            
        def close(self, *args):
            self.set_visible(False)
            self.editor.view.grab_focus()
            # Clear highlights
            if hasattr(self.editor.view, 'set_search_results'):
                self.editor.view.set_search_results([])

        def on_key_pressed(self, controller, keyval, keycode, state):
            if keyval == Gdk.KEY_Escape:
                self.close()
                return True
                
            # Handle Undo/Redo for the editor buffer
            if state & Gdk.ModifierType.CONTROL_MASK:
                if keyval == Gdk.KEY_z or keyval == Gdk.KEY_Z:
                    if state & Gdk.ModifierType.SHIFT_MASK:
                        if hasattr(self.editor.buf, 'redo'):
                            self.editor.buf.redo()
                    else:
                        if hasattr(self.editor.buf, 'undo'):
                            self.editor.buf.undo()
                    return True
                    
                if keyval == Gdk.KEY_y or keyval == Gdk.KEY_Y:
                    if hasattr(self.editor.buf, 'redo'):
                        self.editor.buf.redo()
                    return True
                    
            return False

        def on_search_changed(self, *args):
            # Debounce to prevent excessive searches while typing
            if self._search_timeout_id:
                GLib.source_remove(self._search_timeout_id)
            self._search_timeout_id = GLib.timeout_add(200, self._perform_search)
            
        def _perform_search(self):
            self._search_timeout_id = None
            
            # Cancel any ongoing async search
            if hasattr(self, '_cancel_search') and self._cancel_search:
                self._cancel_search()
                self._cancel_search = None
            
            query = self.find_entry.get_text()
            case_sensitive = self.case_check.get_active()
            is_regex = self.regex_check.get_active()
            whole_word = self.whole_word_check.get_active()
            
            if not query:
                if hasattr(self.editor.view, 'set_search_results'):
                    self.editor.view.set_search_results([])
                self._current_search_query = None
                return False

            # Adjust query for Whole Word
            if whole_word:
                if not is_regex:
                    escaped_query = re.escape(query)
                    query = f"\\b{escaped_query}\\b"
                    is_regex = True
                else:
                    query = f"\\b{query}\\b"
            
            # Store search params for viewport refresh
            self._current_search_query = query
            self._current_search_case = case_sensitive
            self._current_search_regex = is_regex
            
            total_lines = self.editor.buf.total()
            
            # For small files (<50k lines), use synchronous search
            if total_lines < 50000:
                matches = SearchEngine.search(self.editor.buf, query, case_sensitive, is_regex, max_matches=5000)
                if hasattr(self.editor.view, 'set_search_results'):
                    self.editor.view.set_search_results(matches)
                self.update_match_label()
                return False
                
            # For medium files (50k-500k), use async search
            if total_lines < 500000:
                def on_progress(matches, lines_searched, total):
                    if hasattr(self.editor.view, 'set_search_results'):
                        self.editor.view.set_search_results(matches)
                
                def on_complete(matches):
                    self._cancel_search = None
                    if hasattr(self.editor.view, 'set_search_results'):
                        self.editor.view.set_search_results(matches)
                
                self._cancel_search = SearchEngine.search_async(
                    self.editor.buf, query, case_sensitive, is_regex, 
                    max_matches=5000,
                    on_progress=on_progress,
                    on_complete=on_complete,
                    chunk_size=20000
                )
                return False
            
            # For huge files (>500k lines), use viewport-only search
            self._update_viewport_matches()
            
            return False
        
        def _on_editor_scrolled(self):
            """Called when editor scrolls - refresh viewport matches for huge files."""
            if self.editor.buf.total() < 500000:
                return
                
            # Debounce scroll refresh
            if self._scroll_refresh_timeout:
                GLib.source_remove(self._scroll_refresh_timeout)
            self._scroll_refresh_timeout = GLib.timeout_add(100, self._do_scroll_refresh)
        
        def _do_scroll_refresh(self):
            """Debounced scroll refresh of viewport matches."""
            self._scroll_refresh_timeout = None
            self._update_viewport_matches()
            return False
        
        def _update_viewport_matches(self):
            """Update search matches for the current viewport (for huge files)."""
            if not hasattr(self, '_current_search_query') or not self._current_search_query:
                return
                
            # Get visible line range with buffer
            if hasattr(self.editor.view, 'renderer') and hasattr(self.editor.view.renderer, 'line_h'):
                visible_lines = max(50, self.editor.view.get_height() // self.editor.view.renderer.line_h)
            else:
                visible_lines = 50
                
            scroll_line = getattr(self.editor.view, 'scroll_line', 0)
            start_line = max(0, scroll_line - visible_lines)
            end_line = min(self.editor.buf.total() - 1, scroll_line + visible_lines * 2)
            
            matches = SearchEngine.search_viewport(
                self.editor.buf,
                self._current_search_query,
                self._current_search_case,
                self._current_search_regex,
                start_line, end_line,
                max_matches=500
            )
            if hasattr(self.editor.view, 'set_search_results'):
                self.editor.view.set_search_results(matches)

        def on_find_next(self, *args):
            if hasattr(self.editor.view, 'next_match'):
                self.editor.view.next_match()
            self.update_match_label()
            
        def on_find_prev(self, *args):
            if hasattr(self.editor.view, 'prev_match'):
                self.editor.view.prev_match()
            self.update_match_label()
            
        def on_replace(self, *args):
            """Replace current match"""
            replacement = self.replace_entry.get_text()
            
            # Get current match
            if hasattr(self.editor.view, 'current_match') and self.editor.view.current_match:
                # Perform replacement
                if hasattr(self.editor.buf, 'replace_current'):
                    new_match, _ = self.editor.buf.replace_current(self.editor.view.current_match, replacement)
                    
                    # Store old position
                    old_ln, old_col = self.editor.view.current_match[:2]
                    
                    # Re-search
                    self.on_search_changed()
                    
                    # Find the match that comes after old position
                    if hasattr(self.editor.view, 'search_matches') and self.editor.view.search_matches:
                        for i, m in enumerate(self.editor.view.search_matches):
                            ms_ln, ms_col = m[:2]
                            if (ms_ln > old_ln) or (ms_ln == old_ln and ms_col >= old_col + len(replacement)):
                                self.editor.view.current_match_idx = i
                                self.editor.view.current_match = m
                                if hasattr(self.editor.view, '_scroll_to_match'):
                                    self.editor.view._scroll_to_match(m)
                                self.editor.view.queue_draw()
                                break
            
        def on_replace_all(self, *args):
            """Replace all matches"""
            replacement = self.replace_entry.get_text()
            query = self.find_entry.get_text()
            case_sensitive = self.case_check.get_active()
            is_regex = self.regex_check.get_active()
            whole_word = self.whole_word_check.get_active()
            
            # Apply Whole Word logic
            if whole_word:
                if not is_regex:
                    escaped_query = re.escape(query)
                    query = f"\\b{escaped_query}\\b"
                    is_regex = True
                else:
                    query = f"\\b{query}\\b"
            
            total_lines = self.editor.buf.total()
            
            # For huge files, warn and limit
            if total_lines > 500000:
                print(f"Warning: Replace All on {total_lines:,} lines is very slow.")
                matches = SearchEngine.search(self.editor.buf, query, case_sensitive, is_regex, max_matches=10000)
                if not matches:
                    return
                
                count = 0
                self.editor.buf.begin_action()
                try:
                    # Replace in reverse order
                    for i in range(len(matches) - 1, -1, -1):
                        match = matches[i]
                        if hasattr(self.editor.buf, 'replace_current'):
                            self.editor.buf.replace_current(match, replacement, _record_undo=False)
                        count += 1
                finally:
                    self.editor.buf.end_action()
                
                print(f"Replaced {count} occurrences (limited to 10,000)")
                self.on_search_changed()
                return
            
            # Normal replace all
            if hasattr(self.editor.buf, 'replace_all'):
                count = self.editor.buf.replace_all(query, replacement, case_sensitive, is_regex)
            
            self.on_search_changed()

        def update_match_label(self):
            """Update the match counter label"""
            if not hasattr(self.editor.view, 'search_matches'):
                return
                
            matches = self.editor.view.search_matches
            if not matches:
                query = self.find_entry.get_text()
                if query:
                    self.matches_label.set_text("No results")
                    self.matches_label.set_visible(True)
                else:
                    self.matches_label.set_visible(False)
                return

            total = len(matches)
            current_idx = getattr(self.editor.view, 'current_match_idx', -1)
            
            if 0 <= current_idx < total:
                self.matches_label.set_text(f"{current_idx + 1} of {total}")
            else:
                self.matches_label.set_text(f"{total} found")
                
            self.matches_label.set_visible(True)

else:
    # Dummy class if GTK is not available
    class FindReplaceBar:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("GTK4 not available - cannot create FindReplaceBar")


# ============================================================
#   CSS STYLING
# ============================================================

FIND_BAR_CSS = """
/* Find Bar Styling */
.find-bar {
    background-color: @headerbar_bg_color;
    border-bottom: 1px solid alpha(@window_fg_color, 0.15);
    padding: 0px;
}
"""


# ============================================================
#   INSTALLATION / INTEGRATION
# ============================================================

def has_find_support() -> bool:
    """Check if all dependencies are available for find feature"""
    return GTK_AVAILABLE


def install_find_feature(buffer_class: type, view_class: type, editor_page_class: type) -> bool:
    """
    Install find/replace feature into the provided classes.
    
    This function dynamically adds search methods to buffer_class,
    navigation methods to view_class, and integrates FindReplaceBar.
    
    Args:
        buffer_class: The buffer class to add search methods to
        view_class: The view class to add navigation methods to
        editor_page_class: The editor page class (not modified, just for reference)
    
    Returns:
        True if installation successful, False if dependencies missing
    """
    if not has_find_support():
        print("Find feature unavailable: GTK4 not found")
        return False
    
    # Add search methods to buffer class
    buffer_class.search = SearchEngine.search
    buffer_class.search_async = SearchEngine.search_async
    buffer_class.search_viewport = SearchEngine.search_viewport
    
    print("✓ Find/Replace feature installed successfully")
    return True


def get_css() -> str:
    """Get the CSS styling for the find bar"""
    return FIND_BAR_CSS


# ============================================================
#   MODULE INFO
# ============================================================

__version__ = "1.0.0"
__all__ = [
    'FindReplaceBar',
    'SearchEngine',
    'install_find_feature',
    'has_find_support',
    'get_css',
]
