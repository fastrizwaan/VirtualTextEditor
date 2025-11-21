#!/usr/bin/env python3
import sys, os, mmap, gi, cairo, time, unicodedata
from threading import Thread
from array import array
import math
import bisect
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, PangoCairo, GLib, Gio

CSS_OVERLAY_SCROLLBAR = """
/* Vertical container */
.overlay-scrollbar {
    background-color: rgb(25,25,25);
    min-width: 2px;
}

/* Vertical thumb */
.overlay-scrollbar trough > slider {
    min-width: 2px;
    border-radius: 12px;
    background-color: rgba(0,127,255,0.52);
    transition: min-width 200ms ease, background-color 200ms ease;
}

/* Hover → wider */
.overlay-scrollbar trough > slider:hover {
    min-width: 8px;
    background-color: rgba(0,127,255,0.52);
}

/* Dragging → :active (GTK4-native) */
.overlay-scrollbar trough > slider:active {
    min-width: 8px;
    background-color: rgba(255,255,255,0.50);
}


/* ---------------- HORIZONTAL ---------------- */
.hscrollbar-overlay  {
    background-color: rgb(25,25,25);
    min-width: 2px;
}
.hscrollbar-overlay trough > slider {
    min-height: 2px;
    border-radius: 12px;
    background-color: rgba(0,127,255,0.52);
    transition: min-height 200ms ease, background-color 200ms ease;
}

.hscrollbar-overlay trough > slider:hover {
    min-height: 8px;
    background-color: rgba(0,127,255,0.52);
}

/* Dragging (GTK4-native) */
.hscrollbar-overlay trough > slider:active {
    min-height: 8px;
    background-color: rgba(255,255,255,0.50);
}
.editor-surface {
    background-color: rgb(25,25,25); /* same as your renderer’s bg */
}

"""


# ============================================================
#   HELPER FUNCTIONS
# ============================================================

def detect_rtl_line(text):
    """Detect if a line is RTL using Unicode bidirectional properties.
    
    Returns True if the first strong directional character is RTL,
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
#   FULL INDEXING BUT MEMORY-SAFE
# ============================================================

class IndexedFile:
    """
    Fully indexes file once.
    Memory-safe: only stores offsets, not decoded lines.
    Works for UTF-8 and UTF-16 (LE/BE).
    """

    def __init__(self, path):
        print(f"Opening file: {path}")
        start = time.time()
        
        self.path = path
        self.encoding = self.detect_encoding(path)
        self.raw = open(path, "rb")
        self.mm = mmap.mmap(self.raw.fileno(), 0, access=mmap.ACCESS_READ)

        print(f"File opened and mapped in {time.time()-start:.2f}s")
        
        # Use array.array instead of list - much faster for millions of integers
        # 'Q' = unsigned long long (8 bytes, perfect for file offsets)
        self.index = array('Q')

    def detect_encoding(self, path):
        with open(path, "rb") as f:
            data = f.read(4096)  # small peek is enough

        # --- BOM detection ---
        if data.startswith(b"\xff\xfe"):
            return "utf-16le"
        if data.startswith(b"\xfe\xff"):
            return "utf-16be"
        if data.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"

        # --- Heuristic UTF-16LE detection (no BOM) ---
        if len(data) >= 4:
            zeros_in_odd = sum(1 for i in range(1, len(data), 2) if data[i] == 0)
            ratio = zeros_in_odd / (len(data) / 2)
            if ratio > 0.4:
                return "utf-16le"

        # --- Heuristic UTF-16BE detection (no BOM) ---
        zeros_in_even = sum(1 for i in range(0, len(data), 2) if data[i] == 0)
        ratio_be = zeros_in_even / (len(data) / 2)
        if ratio_be > 0.4:
            return "utf-16be"

        # Default
        return "utf-8"


    def index_file(self, progress_callback=None):
        start_time = time.time()
        enc = self.encoding
        
        print(f"Indexing {len(self.mm) / (1024**3):.2f}GB file ({enc})...")

        if enc.startswith("utf-16"):
            self._index_utf16(progress_callback)
        else:
            self._index_utf8(progress_callback)
        
        elapsed = time.time() - start_time
        index_size_mb = len(self.index) * 8 / (1024**2)  # 8 bytes per entry
        
        print(f"Indexed {len(self.index)-1:,} lines in {elapsed:.2f}s ({len(self.mm)/(1024**3)/elapsed:.2f} GB/s)")
        print(f"Average line length: {len(self.mm)/(len(self.index)-1):.0f} bytes")
        print(f"Index memory: {index_size_mb:.1f} MB ({index_size_mb*100/len(self.mm)*1024:.2f}% of file size)")

    def _index_utf8(self, progress_callback=None):
        """Fast UTF-8 indexing using mmap.find() - optimized for huge files"""
        mm = self.mm
        total_size = len(mm)
        
        # Use array.array for fast integer storage (10-20x faster than list for millions of items)
        self.index = array('Q', [0])
        
        # Use mmap.find() to scan for newlines
        pos = 0
        last_report = 0
        report_interval = 50_000_000  # Report every 50MB for less overhead
        
        while pos < total_size:
            # Report progress less frequently (every 50MB instead of 10MB)
            if progress_callback and pos - last_report > report_interval:
                last_report = pos
                progress = pos / total_size
                GLib.idle_add(progress_callback, progress)
            
            # Find next newline directly in mmap (fast C-level search)
            newline_pos = mm.find(b'\n', pos)
            
            if newline_pos == -1:
                # No more newlines
                break
            
            # Record position after the newline
            pos = newline_pos + 1
            self.index.append(pos)
        
        # Ensure file end is recorded
        if not self.index or self.index[-1] != total_size:
            self.index.append(total_size)
        
        if progress_callback:
            GLib.idle_add(progress_callback, 1.0)

    def _index_utf16(self, progress_callback=None):
        """Fast UTF-16 indexing using mmap.find() directly - no memory copies"""
        mm = self.mm
        total_size = len(mm)
        
        # Determine newline pattern based on endianness
        if self.encoding == "utf-16le":
            newline_bytes = b'\n\x00'  # UTF-16LE: \n = 0x0A 0x00
        else:  # utf-16be
            newline_bytes = b'\x00\n'  # UTF-16BE: \n = 0x00 0x0A
        
        # Check for BOM and set start position
        start_pos = 0
        if total_size >= 2:
            first_two = mm[0:2]
            if first_two in (b'\xff\xfe', b'\xfe\xff'):
                start_pos = 2
        
        # Use array.array for fast integer storage
        self.index = array('Q', [start_pos])
        
        # Use mmap.find() to scan for newlines
        pos = start_pos
        last_report = 0
        report_interval = 50_000_000  # Report every 50MB for less overhead
        
        while pos < total_size:
            # Report progress less frequently
            if progress_callback and pos - last_report > report_interval:
                last_report = pos
                progress = pos / total_size
                GLib.idle_add(progress_callback, progress)
            
            # Find next newline directly in mmap (no copy!)
            newline_pos = mm.find(newline_bytes, pos)
            
            if newline_pos == -1:
                # No more newlines
                break
            
            # Record position after the newline (skip the 2-byte newline)
            pos = newline_pos + 2
            self.index.append(pos)
        
        # Ensure file end is recorded
        if not self.index or self.index[-1] != total_size:
            self.index.append(total_size)
        
        if progress_callback:
            GLib.idle_add(progress_callback, 1.0)

    def total_lines(self):
        return len(self.index) - 1

    def __getitem__(self, line):
        if line < 0 or line >= self.total_lines():
            return ""

        start = self.index[line]
        end = self.index[line + 1]

        raw = self.mm[start:end]
        return raw.decode(self.encoding, errors="replace").rstrip("\n\r")


# ============================================================
#   SELECTION
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
#   BUFFER
# ============================================================

class VirtualBuffer(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self):
        super().__init__()
        self.file = None            # IndexedFile
        self.edits = {}             # sparse: logical_line → modified string
        self.deleted_lines = set()  # Track deleted logical lines
        self.inserted_lines = {}    # Track inserted lines: logical_line → content
        self.line_offsets = []      # List of (logical_line, offset) tuples - sorted by logical_line
        self.cursor_line = 0
        self.cursor_col = 0
        self.selection = Selection()

    def load(self, indexed_file):
        self.file = indexed_file
        self.edits.clear()
        self.deleted_lines.clear()
        self.inserted_lines.clear()
        self.line_offsets = []
        self.cursor_line = 0
        self.cursor_col = 0
        self.selection.clear()
        self.emit("changed")


    def _logical_to_physical(self, logical_line):
        """Convert logical line number to physical file line number"""
        if not self.file:
            return logical_line
        
        # Calculate cumulative offset at this logical line
        offset = 0
        for log_line, off in self.line_offsets:
            if log_line <= logical_line:
                offset = off
            else:
                break
        
        return logical_line - offset

    def total(self):
        """Return total number of logical lines in the buffer."""
        if not self.file:
            if not self.edits and not self.inserted_lines:
                return 1
            all_lines = set(self.edits.keys()) | set(self.inserted_lines.keys())
            return max(1, max(all_lines) + 1) if all_lines else 1

        # File is present - file lines plus net insertions
        base = self.file.total_lines()
        
        # Calculate net change from offsets
        if self.line_offsets:
            # The last offset tells us the total shift
            net_insertions = self.line_offsets[-1][1]
            return base + net_insertions
        
        return base

    def get_line(self, ln):
        # Check if it's an inserted line first
        if ln in self.inserted_lines:
            return self.inserted_lines[ln]
        
        # Check if it's an edited line
        if ln in self.edits:
            return self.edits[ln]
        
        # Check if deleted
        if ln in self.deleted_lines:
            return ""
        
        # Convert to physical line and return from file
        if self.file:
            physical = self._logical_to_physical(ln)
            return self.file[physical] if 0 <= physical < self.file.total_lines() else ""
        return ""

    def _add_offset(self, at_line, delta):
        """Add an offset delta starting at logical line at_line"""
        # Find if there's already an offset entry at this line
        found_idx = -1
        for idx, (log_line, offset) in enumerate(self.line_offsets):
            if log_line == at_line:
                found_idx = idx
                break
        
        if found_idx >= 0:
            # Update existing offset
            old_offset = self.line_offsets[found_idx][1]
            self.line_offsets[found_idx] = (at_line, old_offset + delta)
        else:
            # Add new offset entry
            # First, find what the offset was just before this line
            prev_offset = 0
            insert_idx = 0
            for idx, (log_line, offset) in enumerate(self.line_offsets):
                if log_line < at_line:
                    prev_offset = offset
                    insert_idx = idx + 1
                else:
                    break
            
            # Insert new offset entry
            self.line_offsets.insert(insert_idx, (at_line, prev_offset + delta))
        
        # Update all subsequent offset entries
        for idx in range(found_idx + 1 if found_idx >= 0 else insert_idx + 1, len(self.line_offsets)):
            log_line, offset = self.line_offsets[idx]
            self.line_offsets[idx] = (log_line, offset + delta)

    def set_cursor(self, ln, col, extend_selection=False):
        total = self.total()
        ln = max(0, min(ln, total - 1))
        line = self.get_line(ln)
        col = max(0, min(col, len(line)))
        
        if extend_selection:
            # Start selection if not already active
            if not self.selection.active:
                self.selection.set_start(self.cursor_line, self.cursor_col)
            # Update end point
            self.selection.set_end(ln, col)
        else:
            # Clear selection if not extending
            if not self.selection.selecting_with_keyboard:
                self.selection.clear()
        
        self.cursor_line = ln
        self.cursor_col = col

    def select_all(self):
        """Select all text in the buffer"""
        self.selection.set_start(0, 0)
        total = self.total()
        last_line = total - 1
        last_line_text = self.get_line(last_line)
        self.selection.set_end(last_line, len(last_line_text))
        self.cursor_line = last_line
        self.cursor_col = len(last_line_text)
        self.emit("changed")
    
    def get_selected_text(self):
        """Get the currently selected text"""
        if not self.selection.has_selection():
            return ""
        
        start_line, start_col, end_line, end_col = self.selection.get_bounds()
        
        if start_line == end_line:
            line = self.get_line(start_line)
            return line[start_col:end_col]
        else:
            lines = []
            first_line = self.get_line(start_line)
            lines.append(first_line[start_col:])
            
            for ln in range(start_line + 1, end_line):
                lines.append(self.get_line(ln))
            
            last_line = self.get_line(end_line)
            lines.append(last_line[:end_col])
            
            return '\n'.join(lines)
    
    def delete_selection(self):
        """Delete the selected text"""
        if not self.selection.has_selection():
            return False
        
        start_line, start_col, end_line, end_col = self.selection.get_bounds()
        
        if start_line == end_line:
            # Single line selection
            line = self.get_line(start_line)
            new_line = line[:start_col] + line[end_col:]
            
            if start_line in self.inserted_lines:
                self.inserted_lines[start_line] = new_line
            else:
                self.edits[start_line] = new_line
        else:
            # Multi-line selection
            first_line = self.get_line(start_line)
            last_line = self.get_line(end_line)
            new_line = first_line[:start_col] + last_line[end_col:]
            
            # Calculate number of lines being deleted
            lines_deleted = end_line - start_line
            
            # Shift down all virtual lines above the deleted range
            new_ins = {}
            for k, v in self.inserted_lines.items():
                if k < start_line:
                    new_ins[k] = v
                elif k == start_line:
                    # This will be set below
                    pass
                elif k <= end_line:
                    # Skip deleted lines
                    pass
                else:
                    # Shift down
                    new_ins[k - lines_deleted] = v
            
            new_ed = {}
            for k, v in self.edits.items():
                if k < start_line:
                    new_ed[k] = v
                elif k == start_line:
                    # This will be set below
                    pass
                elif k <= end_line:
                    # Skip deleted lines
                    pass
                else:
                    # Shift down
                    new_ed[k - lines_deleted] = v
            
            new_del = set()
            for k in self.deleted_lines:
                if k < start_line:
                    new_del.add(k)
                elif k <= end_line:
                    # Skip deleted lines
                    pass
                else:
                    # Shift down
                    new_del.add(k - lines_deleted)
            
            # Set the merged line
            if start_line in self.inserted_lines:
                new_ins[start_line] = new_line
            else:
                new_ed[start_line] = new_line
            
            self.inserted_lines = new_ins
            self.edits = new_ed
            self.deleted_lines = new_del
            
            # Update line offsets
            self._add_offset(start_line + 1, -lines_deleted)
        
        self.cursor_line = start_line
        self.cursor_col = start_col
        self.selection.clear()
        self.emit("changed")
        return True


    def insert_text(self, text):
        # If there's a selection, delete it first
        if self.selection.has_selection():
            self.delete_selection()

        ln  = self.cursor_line
        col = self.cursor_col
        old = self.get_line(ln)

        # Split insert by newline
        parts = text.split("\n")

        if len(parts) == 1:
            # ---------------------------
            # Simple one-line insert
            # ---------------------------
            new_line = old[:col] + text + old[col:]

            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_line
            else:
                self.edits[ln] = new_line

            self.cursor_col += len(text)
            self.emit("changed")
            return

        # ------------------------------------------------------------
        # Multi-line insert
        # ------------------------------------------------------------
        first = parts[0]
        last  = parts[-1]
        middle = parts[1:-1]   # may be empty

        # Left + first-line fragment
        left_part  = old[:col] + first
        right_part = last + old[col:]

        # Number of new lines being inserted
        lines_to_insert = len(parts) - 1

        # Shift up all virtual lines after current line
        new_ins = {}
        for k, v in self.inserted_lines.items():
            if k < ln:
                new_ins[k] = v
            elif k == ln:
                # This will be set below
                pass
            else:
                # Shift up
                new_ins[k + lines_to_insert] = v

        new_ed = {}
        for k, v in self.edits.items():
            if k < ln:
                new_ed[k] = v
            elif k == ln:
                # This will be set below
                pass
            else:
                # Shift up
                new_ed[k + lines_to_insert] = v

        new_del = set()
        for k in self.deleted_lines:
            if k <= ln:
                new_del.add(k)
            else:
                # Shift up
                new_del.add(k + lines_to_insert)

        # Update current line with left part
        if ln in self.inserted_lines:
            new_ins[ln] = left_part
        else:
            new_ed[ln] = left_part

        # Insert the middle lines
        cur = ln
        for m in middle:
            cur += 1
            new_ins[cur] = m

        # Insert last line (right fragment)
        new_ins[ln + lines_to_insert] = right_part

        # Apply dicts
        self.inserted_lines = new_ins
        self.edits = new_ed
        self.deleted_lines = new_del

        # Offset update (insert count = len(parts)-1)
        self._add_offset(ln + 1, lines_to_insert)

        # Final cursor
        self.cursor_line = ln + lines_to_insert
        self.cursor_col  = len(last)

        self.selection.clear()
        self.emit("changed")



    def backspace(self):
        if self.selection.has_selection():
            self.delete_selection()
            return
        
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col

        if col == 0:
            # Deleting at start of line - merge with previous line
            if ln > 0:
                prev_line = self.get_line(ln - 1)
                new_line = prev_line + line
                
                # Update previous line with merged content
                if ln - 1 in self.inserted_lines:
                    self.inserted_lines[ln - 1] = new_line
                else:
                    self.edits[ln - 1] = new_line
                
                # Shift down all virtual lines after current line
                new_ins = {}
                for k, v in self.inserted_lines.items():
                    if k < ln:
                        new_ins[k] = v
                    elif k == ln:
                        # Skip - this line is being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_ins[k - 1] = v
                
                new_ed = {}
                for k, v in self.edits.items():
                    if k < ln:
                        new_ed[k] = v
                    elif k == ln:
                        # Skip - this line is being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_ed[k - 1] = v
                
                new_del = set()
                for k in self.deleted_lines:
                    if k < ln:
                        new_del.add(k)
                    elif k == ln:
                        # Skip - already being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_del.add(k - 1)
                
                self.inserted_lines = new_ins
                self.edits = new_ed
                self.deleted_lines = new_del
                
                # Track offset change (1 line deleted)
                self._add_offset(ln + 1, -1)
                
                self.cursor_line = ln - 1
                self.cursor_col = len(prev_line)
        else:
            # Normal backspace within a line
            new_line = line[:col-1] + line[col:]
            
            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_line
            else:
                self.edits[ln] = new_line
            
            self.cursor_col = col - 1

        self.selection.clear()
        self.emit("changed")
        


    def delete_key(self):
        """Handle Delete key press"""
        if self.selection.has_selection():
            self.delete_selection()
            return
        
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col
        
        if col >= len(line):
            # At end of line - merge with next line
            if ln < self.total() - 1:
                next_line = self.get_line(ln + 1)
                new_line = line + next_line
                
                # Update current line with merged content
                if ln in self.inserted_lines:
                    self.inserted_lines[ln] = new_line
                else:
                    self.edits[ln] = new_line
                
                # Shift down all virtual lines after next line
                new_ins = {}
                for k, v in self.inserted_lines.items():
                    if k <= ln:
                        new_ins[k] = v
                    elif k == ln + 1:
                        # Skip - this line is being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_ins[k - 1] = v
                
                new_ed = {}
                for k, v in self.edits.items():
                    if k <= ln:
                        new_ed[k] = v
                    elif k == ln + 1:
                        # Skip - this line is being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_ed[k - 1] = v
                
                new_del = set()
                for k in self.deleted_lines:
                    if k <= ln:
                        new_del.add(k)
                    elif k == ln + 1:
                        # Skip - already being deleted
                        pass
                    else:
                        # Shift down by 1
                        new_del.add(k - 1)
                
                self.inserted_lines = new_ins
                self.edits = new_ed
                self.deleted_lines = new_del
                
                # Track offset change (1 line deleted)
                self._add_offset(ln + 2, -1)
        else:
            # Normal delete within a line
            new_line = line[:col] + line[col+1:]
            
            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_line
            else:
                self.edits[ln] = new_line
        
        self.selection.clear()
        self.emit("changed")
        


    def insert_newline(self):
        if self.selection.has_selection():
            self.delete_selection()
        
        ln = self.cursor_line
        col = self.cursor_col

        old_line = self.get_line(ln)
        left = old_line[:col]
        right = old_line[col:]

        # Update current line
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = left
        else:
            self.edits[ln] = left
        
        # Insert new line
        self.inserted_lines[ln + 1] = right
        
        # Track offset change (1 line inserted)
        self._add_offset(ln + 1, 1)
        
        self.cursor_line = ln + 1
        self.cursor_col = 0
        self.selection.clear()
        self.emit("changed")
        


    

    def _logical_to_physical(self, logical_line):
        if not self.file:
            return logical_line

        if not self.line_offsets:
            return logical_line

        # Extract only logical_line keys for binary search
        keys = [lo for lo, _ in self.line_offsets]
        idx = bisect.bisect_right(keys, logical_line) - 1

        if idx < 0:
            return logical_line

        _, offset = self.line_offsets[idx]
        return logical_line - offset

    def _add_offset(self, at_line, delta):
        # Fast path: empty offsets
        if not self.line_offsets:
            self.line_offsets.append((at_line, delta))
            return

        import bisect
        keys = [lo for lo, _ in self.line_offsets]
        pos = bisect.bisect_left(keys, at_line)

        # Case 1: exact match → update
        if pos < len(self.line_offsets) and self.line_offsets[pos][0] == at_line:
            old = self.line_offsets[pos][1]
            new_val = old + delta
            self.line_offsets[pos] = (at_line, new_val)

            # Update following offsets
            for i in range(pos + 1, len(self.line_offsets)):
                lo, off = self.line_offsets[i]
                self.line_offsets[i] = (lo, off + delta)

            return

        # Case 2: insert new offset
        # Find previous offset value
        prev_offset = self.line_offsets[pos-1][1] if pos > 0 else 0

        self.line_offsets.insert(pos, (at_line, prev_offset + delta))

        # Update subsequent offsets
        for i in range(pos + 1, len(self.line_offsets)):
            lo, off = self.line_offsets[i]
            self.line_offsets[i] = (lo, off + delta)

    def insert_newline(self):
        if self.selection.has_selection():
            self.delete_selection()
            return

        ln = self.cursor_line
        col = self.cursor_col

        old = self.get_line(ln)
        left  = old[:col]
        right = old[col:]

        # Update left part
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = left
        else:
            self.edits[ln] = left

        # ---- SHIFT ONLY VIRTUAL LINES ----
        # Inserted
        new_ins = {}
        for k, v in self.inserted_lines.items():
            new_ins[k if k <= ln else k+1] = v

        # Edits
        new_ed = {}
        for k, v in self.edits.items():
            new_ed[k if k <= ln else k+1] = v

        # Deleted
        new_del = set()
        for k in self.deleted_lines:
            new_del.add(k if k <= ln else k+1)

        # Insert right half as NEW line at ln+1
        new_ins[ln + 1] = right

        self.inserted_lines = new_ins
        self.edits = new_ed
        self.deleted_lines = new_del

        # Track logical offset (1 new line)
        self._add_offset(ln + 1, 1)

        # Cursor
        self.cursor_line = ln + 1
        self.cursor_col = 0
        self.selection.clear()
        self.emit("changed")
        
    def move_word_left_with_text(self):
        """Move current word left by swapping with previous word (Alt+Left)"""
        ln = self.cursor_line
        col = self.cursor_col
        line = self.get_line(ln)
        
        if not line:
            return
        
        def is_word_separator(ch):
            """Check if character is a word separator (space, underscore, or punctuation)"""
            return ch in ' _' or not ch.isalnum()
        
        # Find current word boundaries (separated by space, underscore, or punctuation)
        # Find start of current word
        word_start = col
        while word_start > 0 and not is_word_separator(line[word_start - 1]):
            word_start -= 1
        
        # Find end of current word
        word_end = col
        while word_end < len(line) and not is_word_separator(line[word_end]):
            word_end += 1
        
        # Check if we're at the beginning
        if word_start == 0:
            return  # Can't move left
        
        # Find previous word boundaries
        # Skip separators before current word
        prev_word_end = word_start - 1
        while prev_word_end > 0 and is_word_separator(line[prev_word_end]):
            prev_word_end -= 1
        prev_word_end += 1  # Point to position after last char of prev word
        
        if prev_word_end == 0:
            return  # No previous word
        
        # Find start of previous word
        prev_word_start = prev_word_end - 1
        while prev_word_start > 0 and not is_word_separator(line[prev_word_start - 1]):
            prev_word_start -= 1
        
        # Extract words (alphanumeric only)
        current_word = line[word_start:word_end]
        prev_word = line[prev_word_start:prev_word_end]
        
        # Rebuild line with swapped words (keeping separators in place)
        new_line = (line[:prev_word_start] + 
                   current_word + 
                   line[prev_word_end:word_start] +  # separators between words
                   prev_word + 
                   line[word_end:])
        
        # Update line
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = new_line
        else:
            self.edits[ln] = new_line
        
        # Move cursor to the moved word
        self.cursor_col = prev_word_start
        
        # Select the moved word
        self.selection.set_start(ln, prev_word_start)
        self.selection.set_end(ln, prev_word_start + len(current_word))
        
        self.emit("changed")
    
    def move_word_right_with_text(self):
        """Move current word right by swapping with next word (Alt+Right)"""
        ln = self.cursor_line
        col = self.cursor_col
        line = self.get_line(ln)
        
        if not line:
            return
        
        def is_word_separator(ch):
            """Check if character is a word separator (space, underscore, or punctuation)"""
            return ch in ' _' or not ch.isalnum()
        
        # Find current word boundaries (separated by space, underscore, or punctuation)
        # Find start of current word
        word_start = col
        while word_start > 0 and not is_word_separator(line[word_start - 1]):
            word_start -= 1
        
        # Find end of current word
        word_end = col
        while word_end < len(line) and not is_word_separator(line[word_end]):
            word_end += 1
        
        # Skip separators after current word to find next word
        next_word_start = word_end
        while next_word_start < len(line) and is_word_separator(line[next_word_start]):
            next_word_start += 1
        
        # Check if there's a next word
        if next_word_start >= len(line):
            return  # No next word
        
        # Find end of next word
        next_word_end = next_word_start
        while next_word_end < len(line) and not is_word_separator(line[next_word_end]):
            next_word_end += 1
        
        # Extract words (alphanumeric only)
        current_word = line[word_start:word_end]
        next_word = line[next_word_start:next_word_end]
        
        # Rebuild line with swapped words (keeping separators in place)
        new_line = (line[:word_start] + 
                   next_word + 
                   line[word_end:next_word_start] +  # separators between words
                   current_word + 
                   line[next_word_end:])
        
        # Update line
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = new_line
        else:
            self.edits[ln] = new_line
        
        # Move cursor to the moved word's new position
        # After swap: word_start has next_word, then separators, then current_word
        new_current_word_pos = word_start + len(next_word) + (next_word_start - word_end)
        self.cursor_col = new_current_word_pos
        
        # Select the moved word
        self.selection.set_start(ln, new_current_word_pos)
        self.selection.set_end(ln, new_current_word_pos + len(current_word))
        
        self.emit("changed")
    
    def move_line_up_with_text(self):
        """Move current line up one line (Alt+Up)"""
        ln = self.cursor_line
        
        # Check boundary - can't move up if on first line
        if ln == 0:
            return
        
        # Get current and previous line
        current_line = self.get_line(ln)
        prev_line = self.get_line(ln - 1)
        
        # Swap lines
        if ln - 1 in self.inserted_lines:
            self.inserted_lines[ln - 1] = current_line
        else:
            self.edits[ln - 1] = current_line
        
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = prev_line
        else:
            self.edits[ln] = prev_line
        
        # Move cursor to new line position
        self.cursor_line = ln - 1
        self.cursor_col = min(self.cursor_col, len(current_line))
        
        # Select the entire moved line
        self.selection.set_start(ln - 1, 0)
        self.selection.set_end(ln - 1, len(current_line))
        
        self.emit("changed")
    
    def move_line_down_with_text(self):
        """Move current line down one line (Alt+Down)"""
        ln = self.cursor_line
        
        # Check boundary - can't move down if on last line
        if ln >= self.total() - 1:
            return
        
        # Get current and next line
        current_line = self.get_line(ln)
        next_line = self.get_line(ln + 1)
        
        # Swap lines
        if ln in self.inserted_lines:
            self.inserted_lines[ln] = next_line
        else:
            self.edits[ln] = next_line
        
        if ln + 1 in self.inserted_lines:
            self.inserted_lines[ln + 1] = current_line
        else:
            self.edits[ln + 1] = current_line
        
        # Move cursor to new line position
        self.cursor_line = ln + 1
        self.cursor_col = min(self.cursor_col, len(current_line))
        
        # Select the entire moved line
        self.selection.set_start(ln + 1, 0)
        self.selection.set_end(ln + 1, len(current_line))
        
        self.emit("changed")




# ============================================================
#   INPUT
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
        
        # Check what type of character we're on and skip that type
        if is_word_char(line[col]):
            # Skip word characters to the right
            while col < len(line) and is_word_char(line[col]):
                col += 1
        elif not line[col].isspace():
            # Skip symbols/punctuation to the right (treat as a "word")
            while col < len(line) and not line[col].isspace() and not is_word_char(line[col]):
                col += 1
        
        # Skip whitespace to the right
        while col < len(line) and line[col].isspace():
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
#   RENDERER
# ============================================================

class Renderer:
    def __init__(self):
        self.font = Pango.FontDescription("Monospace 12")

        # Correct GTK4/Pango method to compute line height:
        # Use logical extents, not ink extents.
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_text("Ag", -1)  # Reliable glyph pair for height

        ink_rect, logical_rect = layout.get_pixel_extents()

        # These are the correct text and line heights
        self.text_h = logical_rect.height
        self.line_h = self.text_h

        # Track maximum line width for horizontal scrollbar
        self.max_line_width = 0
        self.needs_full_width_scan = False  # Flag to scan all lines after file load</        
        # Colors
        self.editor_background_color = (0.10, 0.10, 0.10)
        self.text_foreground_color   = (0.90, 0.90, 0.90)
        self.linenumber_foreground_color = (0.60, 0.60, 0.60)
        self.selection_background_color = (0.2, 0.4, 0.6)
        self.selection_foreground_color = (1.0, 1.0, 1.0)

    def create_text_layout(self, cr, text="", auto_dir=True):
        """Create a Pango layout with standard settings.
        
        Args:
            cr: Cairo context
            text: Optional text to set
            auto_dir: Whether to enable auto-direction (default True)
            
        Returns:
            Configured Pango layout
        """
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        if auto_dir:
            layout.set_auto_dir(True)
        if text:
            layout.set_text(text, -1)
        return layout

    def calculate_max_line_width(self, cr, buf):
        """Calculate the maximum line width across all lines in the buffer"""
        if not buf:
            self.max_line_width = 0
            return
        
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_auto_dir(True)
        
        max_width = 0
        total = buf.total()
        ln_width = self.calculate_line_number_width(cr, total)
        
        # Check all lines
        for ln in range(total):
            text = buf.get_line(ln)
            if text:
                layout.set_text(text, -1)
                ink, logical = layout.get_pixel_extents()
                text_w = logical.width
                line_total_width = ln_width + text_w
                if line_total_width > max_width:
                    max_width = line_total_width
        
        self.max_line_width = max_width
    
    def get_text_width(self, cr, text):
        """Calculate actual pixel width of text using Pango"""
        if not text:
            return 0
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_text(text, -1)
        width, _ = layout.get_pixel_size()
        return width

    def calculate_text_base_x(self, is_rtl, text_w, view_w, ln_width, scroll_x):
        """Calculate base X position for text rendering.
        
        Args:
            is_rtl: Whether the text is RTL
            text_w: Text width in pixels
            view_w: Viewport width in pixels
            ln_width: Line number column width in pixels
            scroll_x: Horizontal scroll offset
            
        Returns:
            Base X coordinate for text rendering
        """
        if is_rtl:
            available = max(0, view_w - ln_width)
            # Unified formula: right-align and apply scroll offset
            return ln_width + max(0, available - text_w) - scroll_x
        else:
            return ln_width - scroll_x

    def calculate_line_number_width(self, cr, total_lines):
        """Calculate width needed for line numbers based on total lines"""
        # Format the largest line number
        max_line_num = str(total_lines)
        width = self.get_text_width(cr, max_line_num)
        return width + 15  # Add padding (5px left + 10px right margin)

    def draw(self, cr, alloc, buf, scroll_line, scroll_x,
            cursor_visible=True, cursor_phase=0.0):

        import math
        import unicodedata
        
        # If we need a full width scan (e.g., after loading a file), do it first
        if self.needs_full_width_scan and buf:
            self.needs_full_width_scan = False
            layout = self.create_text_layout(cr)
            
            total = buf.total()
            ln_width = self.calculate_line_number_width(cr, total)
            max_width = 0
            
            # Scan first 1000 lines to get a quick estimate
            scan_limit = min(1000, total)
            for ln in range(scan_limit):
                text = buf.get_line(ln)
                if text:
                    layout.set_text(text, -1)
                    ink, logical = layout.get_pixel_extents()
                    text_w = logical.width
                    line_total_width = ln_width + text_w
                    if line_total_width > max_width:
                        max_width = line_total_width
            
            self.max_line_width = max_width

        # Base-direction detection
        def line_is_rtl(text):
            for ch in text:
                t = unicodedata.bidirectional(ch)
                if t in ("L", "LRE", "LRO"):
                    return False
                if t in ("R", "AL", "RLE", "RLO"):
                    return True
            return False

        # Visual UTF-8 byte index for Pango (cluster-correct)
        def visual_byte_index(text, col):
            b = 0
            for ch in text[:col]:
                b += len(ch.encode("utf-8"))
            return b

        # Background
        cr.set_source_rgb(*self.editor_background_color)
        cr.paint()

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.font)
        layout.set_auto_dir(True)

        total = buf.total()
        ln_width = self.calculate_line_number_width(cr, total)
        max_vis = (alloc.height // self.line_h) + 1

        # Get selection bounds if any
        has_selection = buf.selection.has_selection()
        if has_selection:
            sel_start_line, sel_start_col, sel_end_line, sel_end_col = buf.selection.get_bounds()
        else:
            sel_start_line = sel_start_col = sel_end_line = sel_end_col = -1

        # ============================================================
        # DRAW TEXT + LINE NUMBERS + SELECTION
        # ============================================================
        y = 0
        max_width_seen = self.max_line_width  # Start with existing max, don't reset
        
        for ln in range(scroll_line, min(scroll_line + max_vis, total)):
            text = buf.get_line(ln)

            # Line number (LTR always, RIGHT ALIGNED)
            layout.set_auto_dir(False)
            line_num_str = str(ln + 1)
            layout.set_text(line_num_str, -1)
            
            # Get the width of this line number
            line_num_width, _ = layout.get_pixel_size()
            
            # Right align: position at (ln_width - line_num_width - right_padding)
            line_num_x = ln_width - line_num_width - 10  # 10px right padding
            
            cr.set_source_rgb(*self.linenumber_foreground_color)
            cr.move_to(line_num_x, y)
            PangoCairo.show_layout(cr, layout)

            # Prepare for line text
            is_rtl = line_is_rtl(text)
            layout.set_auto_dir(True)
            layout.set_text(text if text else " ", -1)  # Use space for empty lines

            ink, logical = layout.get_pixel_extents()
            text_w = logical.width
            
            # Track maximum width for horizontal scrollbar
            line_total_width = ln_width + text_w
            if line_total_width > max_width_seen:
                max_width_seen = line_total_width

            # Calculate base position
            base_x = self.calculate_text_base_x(is_rtl, text_w, alloc.width, ln_width, scroll_x)
            
            # Set clipping region to prevent text from overlapping line numbers
            cr.save()
            cr.rectangle(ln_width, y, alloc.width - ln_width, self.line_h)
            cr.clip()

            # Draw selection background for this line if needed
            # Draw selection background for this line if needed
            # Draw selection background for this line if needed
            if has_selection and sel_start_line <= ln <= sel_end_line:
                # Calculate selection range for this line
                if ln == sel_start_line and ln == sel_end_line:
                    # Selection within single line
                    start_col = sel_start_col
                    end_col = sel_end_col
                elif ln == sel_start_line:
                    # First line of multi-line selection - select to end + newline indicator
                    start_col = sel_start_col
                    end_col = len(text) + 1  # +1 to include newline visual
                elif ln == sel_end_line:
                    # Last line of multi-line selection - select from start to end_col
                    start_col = 0
                    end_col = sel_end_col
                else:
                    # Middle line - select entire line + newline indicator
                    start_col = 0
                    end_col = len(text) + 1  # +1 to include newline visual
                
                # Calculate pixel positions for selection
                if text or start_col == 0:
                    # Get start position
                    if start_col <= len(text):
                        start_byte = visual_byte_index(text, min(start_col, len(text)))
                        strong_pos, _ = layout.get_cursor_pos(start_byte)
                        sel_start_x = base_x + (strong_pos.x // Pango.SCALE)
                    else:
                        sel_start_x = base_x
                    
                    # Get end position
                    if end_col <= len(text):
                        end_byte = visual_byte_index(text, end_col)
                        strong_pos, _ = layout.get_cursor_pos(end_byte)
                        sel_end_x = base_x + (strong_pos.x // Pango.SCALE)
                    else:
                        # Include newline indicator - extend to viewport end
                        if text:
                            end_byte = visual_byte_index(text, len(text))
                            strong_pos, _ = layout.get_cursor_pos(end_byte)
                            sel_end_x = base_x + (strong_pos.x // Pango.SCALE)
                        else:
                            sel_end_x = base_x
                        
                        # For lines with newline selected, we'll extend to viewport later
                        text_end_x = sel_end_x
                else:
                    # Empty line with selection
                    sel_start_x = base_x
                    text_end_x = base_x
                    sel_end_x = base_x
                
                # Draw main text selection rectangle
                if end_col <= len(text):
                    # Normal selection within text
                    cr.set_source_rgba(*self.selection_background_color, 0.7)
                    if is_rtl:
                        cr.rectangle(min(sel_start_x, sel_end_x), y, 
                                abs(sel_end_x - sel_start_x), self.line_h)
                    else:
                        cr.rectangle(sel_start_x, y, 
                                sel_end_x - sel_start_x, self.line_h)
                    cr.fill()
                else:
                    # Selection includes newline - draw text selection + newline indicator
                    # Draw text selection part
                    if text:
                        cr.set_source_rgba(*self.selection_background_color, 0.7)
                        if is_rtl:
                            cr.rectangle(min(sel_start_x, text_end_x), y, 
                                    abs(text_end_x - sel_start_x), self.line_h)
                        else:
                            cr.rectangle(sel_start_x, y, 
                                    text_end_x - sel_start_x, self.line_h)
                        cr.fill()
                    
                    # Draw newline indicator
                    # For RTL: newline area is on the LEFT (from ln_width to text start)
                    # For LTR: newline area is on the RIGHT (from text end to viewport edge)
                    if is_rtl:
                        # RTL: draw from line number area to start of text
                        if text:
                            # Draw from ln_width to the leftmost edge of the text
                            newline_start_x = ln_width
                            newline_end_x = base_x  # base_x is where RTL text starts
                        else:
                            # Empty line: draw from ln_width to viewport edge
                            newline_start_x = ln_width
                            newline_end_x = alloc.width
                    else:
                        # LTR: draw from text end to viewport edge
                        newline_start_x = text_end_x if text else ln_width
                        newline_end_x = alloc.width
                    
                    # Use slightly darker/different shade for newline area
                    cr.set_source_rgba(*self.selection_background_color, 0.7)
                    cr.rectangle(newline_start_x, y, 
                            newline_end_x - newline_start_x, self.line_h)
                    cr.fill()
                    
                    # Draw a subtle vertical line at the end of actual text to mark the newline position
                    # Don't really need it disabled it with 0.0
                    if text:
                        cr.set_source_rgba(*self.selection_foreground_color, 0.0)
                        cr.set_line_width(1)
                        cr.move_to(text_end_x, y)
                        cr.line_to(text_end_x, y + self.line_h)
                        cr.stroke()

            # Draw line text
            if text:  # Only draw if there's actual text
                cr.set_source_rgb(*self.text_foreground_color)
                cr.move_to(base_x, y)
                layout.set_text(text, -1)
                PangoCairo.show_layout(cr, layout)
            
            # Restore clipping region
            cr.restore()

            y += self.line_h

        # Update tracked maximum line width for horizontal scrollbar
        self.max_line_width = max_width_seen
        
        # ============================================================
        # PREEDIT (IME)
        # ============================================================
        cl, cc = buf.cursor_line, buf.cursor_col
        line_visible = (scroll_line <= cl < scroll_line + max_vis)

        if hasattr(buf, "preedit_string") and buf.preedit_string and line_visible:
            py = (cl - scroll_line) * self.line_h
            line_text = buf.get_line(cl)

            pe_l = self.create_text_layout(cr, line_text if line_text else " ")

            is_rtl = detect_rtl_line(line_text)
            text_w, _ = pe_l.get_pixel_size()
            base_x = self.calculate_text_base_x(is_rtl, text_w, alloc.width, ln_width, scroll_x)

            byte_index = visual_byte_index(line_text, cc)
            strong_pos, weak_pos = pe_l.get_cursor_pos(byte_index)
            cursor_x = strong_pos.x // Pango.SCALE
            px = base_x + cursor_x

            # Preedit text
            pe_l.set_text(buf.preedit_string, -1)
            cr.set_source_rgba(1, 1, 1, 0.7)
            cr.move_to(px, py)
            PangoCairo.show_layout(cr, pe_l)

            uw, _ = pe_l.get_pixel_size()
            cr.set_line_width(1.0)
            cr.move_to(px, py + self.text_h)
            cr.line_to(px + uw, py + self.text_h)
            cr.stroke()

            # Preedit cursor
            if hasattr(buf, "preedit_cursor"):
                pc = buf.preedit_cursor

                pe_l2 = PangoCairo.create_layout(cr)
                pe_l2.set_font_description(self.font)
                pe_l2.set_auto_dir(True)
                pe_l2.set_text(buf.preedit_string, -1)

                byte_index2 = visual_byte_index(buf.preedit_string, pc)
                strong_pos2, weak_pos2 = pe_l2.get_cursor_pos(byte_index2)
                cw = strong_pos2.x // Pango.SCALE

                cr.set_line_width(1.0)
                cr.move_to(px + cw, py)
                cr.line_to(px + cw, py + self.text_h)
                cr.stroke()

        # ============================================================
        # CURSOR
        # ============================================================
        if cursor_visible and line_visible:
            cursor_text = buf.get_line(cl)
            from_scroll = cl - scroll_line
            cy = from_scroll * self.line_h

            # Use same layout logic as above
            layout = self.create_text_layout(cr, cursor_text if cursor_text else " ")
            is_rtl = detect_rtl_line(cursor_text)
            text_w, _ = layout.get_pixel_size()
            view_w = alloc.width
            base_x = self.calculate_text_base_x(is_rtl, text_w, view_w, ln_width, scroll_x)

            byte_idx = visual_byte_index(cursor_text, cc)
            strong_pos, _ = layout.get_cursor_pos(byte_idx)
            cx = base_x + (strong_pos.x // Pango.SCALE)

            # Draw cursor line (small vertical bar)
            phase = cursor_phase if cursor_phase is not None else 0.0
            alpha = 0.3 + 0.7 * phase
            cr.set_source_rgba(0, 0.5, 1.0, alpha)
            cr.set_line_width(2)
            cr.move_to(cx, cy)
            cr.line_to(cx, cy + self.line_h)
            cr.stroke()
        
        # ============================================================
        # DRAG-AND-DROP PREVIEW OVERLAY
        # ============================================================
        # Draw preview overlay at drop position
        if hasattr(buf, '_view') and buf._view:
            view = buf._view
            if view.drag_and_drop_mode and view.drop_position_line >= 0:
                drop_ln = view.drop_position_line
                drop_col = view.drop_position_col
                
                # Check if drop position is within original selection (no-op)
                drop_in_selection = False
                if buf.selection.has_selection():
                    bounds = buf.selection.get_bounds()
                    if bounds and bounds[0] is not None:
                        sel_start_line, sel_start_col, sel_end_line, sel_end_col = bounds
                        
                        if sel_start_line == sel_end_line:
                            # Single line selection
                            if drop_ln == sel_start_line and sel_start_col <= drop_col <= sel_end_col:
                                drop_in_selection = True
                        else:
                            # Multi-line selection
                            if drop_ln == sel_start_line and drop_col >= sel_start_col:
                                drop_in_selection = True
                            elif drop_ln == sel_end_line and drop_col <= sel_end_col:
                                drop_in_selection = True
                            elif sel_start_line < drop_ln < sel_end_line:
                                drop_in_selection = True
                
                # Draw overlay even if over selection, but skip cursor
                if scroll_line <= drop_ln < scroll_line + max_vis:
                    drop_y = (drop_ln - scroll_line) * self.line_h
                    drop_text = buf.get_line(drop_ln)
                    
                    # Calculate drop position
                    layout = self.create_text_layout(cr, drop_text if drop_text else " ")
                    is_rtl = detect_rtl_line(drop_text)
                    text_w, _ = layout.get_pixel_size()
                    view_w = alloc.width
                    base_x = self.calculate_text_base_x(is_rtl, text_w, view_w, ln_width, scroll_x)
                    
                    # Get x position for drop column
                    drop_byte_idx = visual_byte_index(drop_text, min(drop_col, len(drop_text)))
                    strong_pos, _ = layout.get_cursor_pos(drop_byte_idx)
                    drop_x = base_x + (strong_pos.x // Pango.SCALE)
                    
                    # Determine colors based on copy (Ctrl) vs move mode
                    is_copy = view.ctrl_pressed_during_drag
                    if is_copy:
                        # Green for copy
                        cursor_color = (0.0, 1.0, 0.3, 0.9)
                        bg_color = (0.0, 0.8, 0.3, 1.0)  # Opaque green background
                        border_color = (0.0, 1.0, 0.3, 1.0)
                    else:
                        # Orange for move
                        cursor_color = (1.0, 0.6, 0.0, 0.9)
                        bg_color = (1.0, 0.5, 0.0, 1.0)  # Opaque orange background
                        border_color = (1.0, 0.6, 0.0, 1.0)
                    
                    # Draw cursor at drop position ONLY if not over selection
                    if not drop_in_selection:
                        cr.set_source_rgba(*cursor_color)
                        cr.set_line_width(2)
                        cr.move_to(drop_x, drop_y)
                        cr.line_to(drop_x, drop_y + self.line_h)
                        cr.stroke()
                    
                    # Draw viewport border (1 pixel) - always show
                    cr.set_source_rgba(*border_color)
                    cr.set_line_width(1)
                    cr.rectangle(0, 0, alloc.width, alloc.height)
                    cr.stroke()
                    
                    # Draw the dragged text as overlay with background (no border) - always show
                    dragged_text = view.dragged_text
                    if dragged_text:
                        # Check if multi-line selection
                        is_multiline = '\n' in dragged_text
                        
                        # Create layout for dragged text
                        overlay_layout = self.create_text_layout(cr, dragged_text)
                        overlay_w, overlay_h = overlay_layout.get_pixel_size()
                        
                        # Offset the overlay below the cursor so pointer is above it
                        vertical_offset = 20  # Pixels below the cursor
                        drop_y_offset = drop_y + vertical_offset
                        
                        # Draw background only for single-line selections
                        if not is_multiline:
                            padding = 4
                            cr.set_source_rgba(*bg_color)
                            cr.rectangle(drop_x - padding, drop_y_offset - padding, 
                                       overlay_w + 2*padding, self.line_h + 2*padding)
                            cr.fill()
                        
                        # Draw the text with transparency
                        r, g, b = self.text_foreground_color
                        cr.set_source_rgba(r, g, b, 0.7)  # 70% opacity
                        cr.move_to(drop_x, drop_y_offset)
                        PangoCairo.show_layout(cr, overlay_layout)


# ============================================================
#   VIEW
# ============================================================

class VirtualTextView(Gtk.DrawingArea):

    def __init__(self, buf):
        super().__init__()
        self.buf = buf
        # Add reference from buffer to view for drag-and-drop
        buf._view = self
        self.renderer = Renderer()
        self.ctrl = InputController(self, buf)
        self.scroll_line = 0
        self.scroll_x = 0

        self.set_focusable(True)
        self.set_vexpand(True)
        self.set_hexpand(True)
        self.set_draw_func(self.draw_view)

        self.install_mouse()
        self.install_keys()
        self.install_im()

    def create_text_layout(self, cr, text="", auto_dir=True):
        """Create a Pango layout using renderer's font.
        
        Args:
            cr: Cairo context
            text: Optional text to set
            auto_dir: Whether to enable auto-direction (default True)
            
        Returns:
            Configured Pango layout
        """
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.renderer.font)
        if auto_dir:
            layout.set_auto_dir(True)
        if text:
            layout.set_text(text, -1)
        return layout

    def install_im(self):
        self.install_scroll()
        self.hadj = Gtk.Adjustment(
            value=0, lower=0, upper=1, step_increment=20, page_increment=200, page_size=100
        )
        self.vadj = Gtk.Adjustment(
            value=0, lower=0, upper=1, step_increment=1, page_increment=10, page_size=1
        )
        self.vadj.connect("value-changed", self.on_vadj_changed)
        self.hadj.connect("value-changed", self.on_hadj_changed)

        self.buf.connect("changed", self.on_buffer_changed)


        # Setup IM context with preedit support
        self.im = Gtk.IMMulticontext()
        self.im.connect("commit", self.on_commit)
        self.im.connect("preedit-changed", self.on_preedit_changed)
        self.im.connect("preedit-start", self.on_preedit_start)
        self.im.connect("preedit-end", self.on_preedit_end)
        self.connect("resize", self.on_resize)

        # Preedit state
        self.preedit_string = ""
        self.preedit_cursor = 0
        
        # Connect focus events
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self.on_focus_in)
        focus.connect("leave", self.on_focus_out)
        self.add_controller(focus)
        
        # Cursor blink state
        # Cursor blink state (smooth fade)
        self.cursor_visible = True
        self.cursor_blink_timeout = None

        self.cursor_phase = 0.0           # animation phase 0 → 2
        self.cursor_fade_speed = 0.03     # 0.02 ~ 50fps smooth fade

        self.start_cursor_blink()
        
        # Connect to size changes to update scrollbars
        self.connect('resize', self.on_resize)

    def on_buffer_changed(self, *args):
        # Update scrollbars after width changes
        self.update_scrollbar()
        self.queue_draw()


    def on_vadj_changed(self, adj):
        # When scrollbar moves → update internal scroll line
        new = int(adj.get_value())
        if new != self.scroll_line:
            self.scroll_line = new
            self.queue_draw()

    def on_hadj_changed(self, adj):
        # When scrollbar moves → update internal scroll offset
        new = int(adj.get_value())
        if new != self.scroll_x:
            self.scroll_x = new
            self.queue_draw()
                
    def on_resize(self, widget, width, height):
        """Handle window resize to update scrollbar visibility"""
        self.update_scrollbar()
        return False

    def file_loaded(self):
        """Called after a new file is loaded to trigger width calculation"""
        self.renderer.needs_full_width_scan = True
        self.queue_draw()
        self.update_scrollbar()
        
    def update_scrollbar(self):
        width = self.get_width()
        height = self.get_height()
        if width <= 0 or height <= 0:
            return

        # TRUE viewport width in GTK4 (overlay scrollbars do NOT consume space)
        viewport_width = width

        # vertical
        total_lines = self.buf.total()
        line_h = self.renderer.line_h
        visible = max(1, height // line_h)

        self.vadj.set_lower(0)
        self.vadj.set_upper(total_lines)
        self.vadj.set_page_size(visible)
        self.vadj.set_step_increment(1)
        self.vadj.set_page_increment(visible)

        max_scroll = max(0, total_lines - visible)
        if self.scroll_line > max_scroll:
            self.scroll_line = max_scroll
            self.vadj.set_value(self.scroll_line)

        # horizontal
        doc_w = self.renderer.max_line_width

        self.hadj.set_lower(0)
        self.hadj.set_upper(doc_w)
        self.hadj.set_page_size(viewport_width)
        self.hadj.set_step_increment(20)
        self.hadj.set_page_increment(viewport_width // 2)

        max_hscroll = max(0, doc_w - viewport_width)
        if self.scroll_x > max_hscroll:
            self.scroll_x = max_hscroll
            self.hadj.set_value(self.scroll_x)

        def finalize():
            self.vscroll.set_visible(total_lines > visible)
            self.hscroll.set_visible(doc_w > viewport_width)
            return False

        GLib.idle_add(finalize)



    # Correct UTF-8 byte-index for logical col → Pango visual mapping
    def visual_byte_index(self, text, col):
        b = 0
        for ch in text[:col]:
            b += len(ch.encode("utf-8"))
        return b

    def pixel_to_column(self, cr, text, px):
        """Convert pixel position to column index, handling end-of-line"""
        if not text:
            return 0
            
        layout = self.create_text_layout(cr, text)

        # Get total text width
        text_w, _ = layout.get_pixel_size()
        
        # If clicking beyond text, return end of line
        if px >= text_w:
            return len(text)

        # Convert to Pango units
        success, index, trailing = layout.xy_to_index(px * Pango.SCALE, 0)
        if not success:
            # Clicked outside text bounds
            if px < 0:
                return 0
            else:
                return len(text)

        # index = byte offset → convert back to UTF-8 column
        substr = text.encode("utf-8")[:index]
        try:
            col = len(substr.decode("utf-8"))
            # Add trailing characters (for clicking on right side of character)
            col += trailing
            return min(col, len(text))
        except:
            return len(text)


    def start_cursor_blink(self):
        # Always start blinking from fully visible
        self.cursor_phase = 0.0

        def blink():
            self.cursor_phase += self.cursor_fade_speed
            if self.cursor_phase >= 2.0:
                self.cursor_phase -= 2.0

            self.queue_draw()
            return True

        if self.cursor_blink_timeout:
            GLib.source_remove(self.cursor_blink_timeout)

        self.cursor_blink_timeout = GLib.timeout_add(20, blink)


    def stop_cursor_blink(self):
        if self.cursor_blink_timeout:
            GLib.source_remove(self.cursor_blink_timeout)
            self.cursor_blink_timeout = None

        self.cursor_visible = True
        self.cursor_phase = 0.0   # NOT 1.0
        self.queue_draw()



    def on_commit(self, im, text):
        """Handle committed text from IM (finished composition)"""
        if text:
            # Insert typed text
            self.buf.insert_text(text)

            # Keep cursor on screen
            self.keep_cursor_visible()

            # While typing → cursor MUST be solid
            self.cursor_visible = True
            self.cursor_phase = 0.0     # brightest point of fade

            # Stop any blinking while typing
            self.stop_cursor_blink()

            # Blink will resume after user stops typing
            self.restart_blink_after_idle()

            # Redraw + update IME
            self.queue_draw()
            self.update_im_cursor_location()


    def restart_blink_after_idle(self):
        def idle_blink():
            self.start_cursor_blink()
            return False  # one-shot
        GLib.timeout_add(700, idle_blink)  # restart after 700ms idle




    def on_preedit_start(self, im):
        """Preedit (composition) started"""
        self.queue_draw()

    def on_preedit_end(self, im):
        """Preedit (composition) ended"""
        self.preedit_string = ""
        self.preedit_cursor = 0
        self.queue_draw()

    def on_preedit_changed(self, im):
        """Preedit text changed - show composition"""
        try:
            preedit_str, attrs, cursor_pos = self.im.get_preedit_string()
            self.preedit_string = preedit_str or ""
            self.preedit_cursor = cursor_pos
            self.queue_draw()
        except Exception as e:
            print(f"Preedit error: {e}")

    def on_focus_in(self, controller):
        """Widget gained focus"""
        self.im.focus_in()
        self.im.set_client_widget(self)
        self.update_im_cursor_location()
        
    def on_focus_out(self, controller):
            self.im.focus_out()

    def update_im_cursor_location(self):
        try:
            width  = self.get_width()
            height = self.get_height()
            if width <= 0 or height <= 0:
                return

            cl, cc = self.buf.cursor_line, self.buf.cursor_col
            line_text = self.buf.get_line(cl)

            # Pango layout
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)

            layout = self.create_text_layout(cr, line_text if line_text else " ")

            is_rtl = detect_rtl_line(line_text)
            text_w, _ = layout.get_pixel_size()
            ln_w = self.renderer.calculate_line_number_width(cr, self.buf.total())

            # base_x matches draw()
            base_x = self.renderer.calculate_text_base_x(is_rtl, text_w, width, ln_w, self.scroll_x)

            # ---- FIXED: correct UTF-8 byte index ----
            byte_index = self.visual_byte_index(line_text, cc)

            strong_pos, weak_pos = layout.get_cursor_pos(byte_index)
            cursor_x = strong_pos.x // Pango.SCALE

            x = base_x + cursor_x
            y = (cl - self.scroll_line) * self.renderer.line_h

            # clamp
            if y < 0 or y > height - self.renderer.text_h:
                return

            x = max(ln_w, min(x, width - 50))
            y = max(0,     min(y, height - self.renderer.text_h))

            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width  = 2
            rect.height = self.renderer.text_h

            self.im.set_cursor_location(rect)

        except Exception as e:
            print(f"IM cursor location error: {e}")

                
    def on_key(self, c, keyval, keycode, state):
        # Let IM filter the event FIRST
        event = c.get_current_event()
        if event and self.im.filter_keypress(event):
            return True


        name = Gdk.keyval_name(keyval)
        shift_pressed = (state & Gdk.ModifierType.SHIFT_MASK) != 0
        ctrl_pressed = (state & Gdk.ModifierType.CONTROL_MASK) != 0
        alt_pressed = (state & Gdk.ModifierType.ALT_MASK) != 0

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
        
        # Tab key - insert tab character
        if name == "Tab":
            self.buf.insert_text("\t")
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Editing keys
        if name == "BackSpace":
            self.buf.backspace()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Delete":
            self.buf.delete_key()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Return":
            self.buf.insert_newline()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            self.update_scrollbar() 
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

    def copy_to_clipboard(self):
        """Copy selected text to clipboard"""
        text = self.buf.get_selected_text()
        if text:
            clipboard = self.get_clipboard()
            clipboard.set_content(Gdk.ContentProvider.new_for_value(text))

    def cut_to_clipboard(self):
        """Cut selected text to clipboard"""
        text = self.buf.get_selected_text()
        if text:
            clipboard = self.get_clipboard()
            clipboard.set_content(Gdk.ContentProvider.new_for_value(text))
            self.buf.delete_selection()
            self.queue_draw()

    def paste_from_clipboard(self):
        """Paste text from clipboard with better error handling"""
        clipboard = self.get_clipboard()
        
        def paste_ready(clipboard, result):
            try:
                text = clipboard.read_text_finish(result)
                if text:
                    self.buf.insert_text(text)
                    self.keep_cursor_visible()
                    self.update_im_cursor_location()
                    self.queue_draw()
            except Exception as e:
                error_msg = str(e)
                # Silently ignore "No compatible transfer format" errors
                # This happens when clipboard contains non-text data (images, etc.)
                if "No compatible transfer format" not in error_msg:
                    print(f"Paste error: {e}")
                # Optionally try to get text in a different way
                self.try_paste_fallback()
        
        clipboard.read_text_async(None, paste_ready)

    def try_paste_fallback(self):
        """Fallback method to try getting clipboard text"""
        try:
            clipboard = self.get_clipboard()
            
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
                                self.buf.insert_text(text)
                                self.keep_cursor_visible()
                                self.update_im_cursor_location()
                                self.queue_draw()
                    except Exception as e:
                        # Silently fail - clipboard probably contains non-text data
                        pass
                
                clipboard.read_async(["text/plain"], 0, None, read_ready)
        except Exception as e:
            # Silently fail - this is just a fallback attempt
            pass

    def install_keys(self):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self.on_key)
        key.connect("key-released", self.on_key_release)
        self.add_controller(key)
        
    def on_key_release(self, c, keyval, keycode, state):
        """Filter key releases for IM"""
        event = c.get_current_event()
        if event and self.im.filter_keypress(event):
            return True
        return False


    def install_mouse(self):
        click = Gtk.GestureClick()
        click.set_button(1)
        click.connect("pressed", self.on_click_pressed)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)
        
        # Middle-click paste
        middle_click = Gtk.GestureClick()
        middle_click.set_button(2)  # Middle mouse button
        middle_click.connect("pressed", self.on_middle_click)
        self.add_controller(middle_click)
        
        # Right-click menu
        right_click = Gtk.GestureClick()
        right_click.set_button(3)  # Right mouse button
        right_click.connect("pressed", self.on_right_click)
        self.add_controller(right_click)
        
        # Track last click time and position for multi-click detection
        self.last_click_time = 0
        self.last_click_line = -1
        self.last_click_col = -1
        self.click_count = 0
        
        # Track word selection mode for drag-to-select-words
        self.word_selection_mode = False
        
        # Track the original anchor word boundaries (for stable bi-directional drag)
        self.anchor_word_start_line = -1
        self.anchor_word_start_col = -1
        self.anchor_word_end_line = -1
        self.anchor_word_end_col = -1
        
        # Track drag-and-drop mode for moving/copying selected text
        self.drag_and_drop_mode = False
        self.dragged_text = ""
        self.drop_position_line = -1
        self.drop_position_col = -1
        self.ctrl_pressed_during_drag = False  # Track if Ctrl is pressed during drag
        
        # Track if we clicked inside a selection (to handle click-to-clear vs drag)
        self._clicked_in_selection = False
        
        # Track if a drag might start (deferred until movement)
        self._drag_pending = False

    def on_middle_click(self, gesture, n_press, x, y):
        """Paste from primary clipboard on middle-click"""
        self.grab_focus()
        
        # Get click position
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
                self.keep_cursor_visible()
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

    def on_click_pressed(self, g, n_press, x, y):
        self.grab_focus()

        ln, col = self.xy_to_line_col(x, y)
        mods = g.get_current_event_state()
        shift = bool(mods & Gdk.ModifierType.SHIFT_MASK)

        # --- Multi-click timing ---
        import time
        current_time = time.time()
        time_diff = current_time - self.last_click_time

        if time_diff > 0.5 or ln != self.last_click_line or abs(col - self.last_click_col) > 3:
            self.click_count = 0

        self.click_count += 1
        self.last_click_time = current_time
        self.last_click_line = ln
        self.last_click_col = col

        line_text = self.buf.get_line(ln)
        line_len = len(line_text)

        # ----------------------------------------------------------
        # SHIFT EXTEND (unchanged)
        # ----------------------------------------------------------
        if shift:
            if not self.buf.selection.active:
                self.buf.selection.set_start(self.buf.cursor_line, self.buf.cursor_col)
            self.buf.selection.set_end(ln, col)
            self.buf.set_cursor(ln, col, extend_selection=True)
            self.queue_draw()
            return

        # ----------------------------------------------------------
        # TRIPLE CLICK → select entire textual line (unchanged)
        # ----------------------------------------------------------
        if self.click_count == 3:
            self.buf.selection.set_start(ln, 0)
            self.buf.selection.set_end(ln, line_len)
            self.buf.cursor_line = ln
            self.buf.cursor_col = line_len
            self.queue_draw()
            return

        # ----------------------------------------------------------
        # DOUBLE CLICK behavior
        # ----------------------------------------------------------
        if self.click_count == 2:

            # Case 1: empty line → context-aware selection
            if line_len == 0:
                # Check what comes next
                next_line_text = None
                if ln < self.buf.total() - 1:
                    next_line_text = self.buf.get_line(ln + 1)
                
                if next_line_text is not None and len(next_line_text) == 0:
                    # Next line is also empty: select only current empty line
                    self.buf.selection.set_start(ln, 0)
                    self.buf.selection.set_end(ln, 1)
                    self.buf.cursor_line = ln
                    self.buf.cursor_col = 0
                elif next_line_text is not None and len(next_line_text) > 0:
                    # Next line has text: select current empty line + next line's text
                    self.buf.selection.set_start(ln, 0)
                    self.buf.selection.set_end(ln + 1, len(next_line_text))
                    self.buf.cursor_line = ln + 1
                    self.buf.cursor_col = len(next_line_text)
                else:
                    # Last line (empty): don't select anything
                    self.buf.selection.clear()
                    self.buf.cursor_line = ln
                    self.buf.cursor_col = 0
                
                # Enable word selection mode for drag (treat empty lines as "words")
                self.word_selection_mode = True
                
                self.queue_draw()
                return

            # Case 2: double-click beyond end of text
            if col > line_len:
                # Check if this line has a newline (not the last line)
                has_newline = ln < self.buf.total() - 1
                
                if has_newline:
                    # Line has newline: select the newline area
                    self.buf.selection.set_start(ln, line_len)
                    self.buf.selection.set_end(ln, line_len + 1)
                    self.buf.cursor_line = ln
                    self.buf.cursor_col = line_len
                else:
                    # Last line (no newline): select trailing content
                    # Find what's at the end: word or spaces
                    if line_text and line_text[-1] == ' ':
                        # Find start of trailing spaces
                        start = line_len - 1
                        while start > 0 and line_text[start - 1] == ' ':
                            start -= 1
                        self.buf.selection.set_start(ln, start)
                        self.buf.selection.set_end(ln, line_len)
                        self.buf.cursor_line = ln
                        self.buf.cursor_col = line_len
                    else:
                        # Select the last word
                        start_col, end_col = self.find_word_boundaries(line_text, line_len - 1)
                        self.buf.selection.set_start(ln, start_col)
                        self.buf.selection.set_end(ln, end_col)
                        self.buf.cursor_line = ln
                        self.buf.cursor_col = end_col
                
                # Enable word selection mode for drag
                self.word_selection_mode = True
                
                self.queue_draw()
                return

            # Case 3: normal double-click → word selection (unchanged)
            start_col, end_col = self.find_word_boundaries(line_text, col)
            self.buf.selection.set_start(ln, start_col)
            self.buf.selection.set_end(ln, end_col)
            self.buf.cursor_line = ln
            self.buf.cursor_col = end_col
            
            # Enable word selection mode for drag AND store anchor word
            self.word_selection_mode = True
            self.anchor_word_start_line = ln
            self.anchor_word_start_col = start_col
            self.anchor_word_end_line = ln
            self.anchor_word_end_col = end_col
            
            self.queue_draw()
            return

        # ----------------------------------------------------------
        # SINGLE CLICK (unchanged)
        # ----------------------------------------------------------
        # Check if clicking inside existing selection - if so, defer clearing
        # until we know it's not a drag operation
        if self.buf.selection.has_selection():
            bounds = self.buf.selection.get_bounds()
            if bounds and bounds[0] is not None:
                start_line, start_col, end_line, end_col = bounds
                
                # Check if click is within selection
                click_in_selection = False
                if start_line == end_line:
                    if ln == start_line and start_col <= col < end_col:
                        click_in_selection = True
                else:
                    if ln == start_line and col >= start_col:
                        click_in_selection = True
                    elif ln == end_line and col < end_col:
                        click_in_selection = True
                    elif start_line < ln < end_line:
                        click_in_selection = True
                
                if click_in_selection:
                    # Don't clear selection yet - might be starting a drag
                    # Just update cursor position
                    self.buf.cursor_line = ln
                    self.buf.cursor_col = col
                    self._clicked_in_selection = True
                    self.queue_draw()
                    return
        
        # Normal single click - clear selection and start new drag
        self._clicked_in_selection = False
        self.buf.selection.clear()
        self.ctrl.start_drag(ln, col)
        
        # Set pending click for release handler
        self._pending_click = True
        self._click_ln = ln
        self._click_col = col
        
        # Note: Don't clear word_selection_mode here! 
        # It will be cleared in on_drag_begin if needed
        
        self.queue_draw()


    def on_click(self, g, n, x, y):
        self.grab_focus()

        # Get modifiers
        modifiers = g.get_current_event_state()
        shift_pressed = (modifiers & Gdk.ModifierType.SHIFT_MASK) != 0

        # Temporary Pango context
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())

        ln = self.scroll_line + int(y // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        # Calculate column position
        
        text = self.buf.get_line(ln)
        
        # Create layout for this line
        layout = self.create_text_layout(cr, text if text else " ")
        
        is_rtl = detect_rtl_line(text)
        text_w, _ = layout.get_pixel_size()
        view_w = self.get_width()
        
        # Calculate base_x matching the renderer
        base_x = self.renderer.calculate_text_base_x(is_rtl, text_w, view_w, ln_width, self.scroll_x)
        
        # Calculate relative pixel position from base
        col_pixels = x - base_x
        col_pixels = max(0, col_pixels)

        # Convert pixel to column
        col = self.pixel_to_column(cr, text, col_pixels)
        col = max(0, min(col, len(text)))

        # Handle shift-click for selection
        if shift_pressed:
            # Extend selection from current cursor position
            if not self.buf.selection.active:
                self.buf.selection.set_start(self.buf.cursor_line, self.buf.cursor_col)
            self.buf.selection.set_end(ln, col)
            self.buf.set_cursor(ln, col, extend_selection=True)
        else:
        # Normal click - clear selection and move cursor
            self.ctrl.click(ln, col)
        
        self.queue_draw()

    def on_release(self, g, n, x, y):
        """Handle mouse button release"""
        self.ctrl.end_drag()



    def on_drag_begin(self, g, x, y):
        ln, col = self.xy_to_line_col(x, y)
        
        # Check if clicking on selected text
        if self.buf.selection.has_selection():
            start_line, start_col, end_line, end_col = self.buf.selection.get_bounds()
            
            # Check if click is within selection
            click_in_selection = False
            if start_line == end_line:
                # Single line selection
                if ln == start_line and start_col <= col < end_col:
                    click_in_selection = True
            else:
                # Multi-line selection
                if ln == start_line and col >= start_col:
                    click_in_selection = True
                elif ln == end_line and col < end_col:
                    click_in_selection = True
                elif start_line < ln < end_line:
                    click_in_selection = True
            
            if click_in_selection:
                # We might be starting a drag, but wait for actual movement
                self._drag_pending = True
                # Don't set drag_and_drop_mode yet - wait for on_drag_update
                self.drag_and_drop_mode = False
                
                # Store the selected text (just in case)
                self.dragged_text = self.buf.get_selected_text()
                
                # Don't start normal selection drag - this preserves the selection
                # Don't call ctrl.start_drag() to keep selection visible
                return
        
        # Normal drag behavior
        self.drag_and_drop_mode = False
        self._drag_pending = False
        self._pending_click = False  # We are dragging, so cancel pending click
        
        if self.word_selection_mode:
            # In word selection mode (after double-click), we want to KEEP the current selection
            # and just start dragging from here.
            # So we manually set dragging state without clearing selection via start_drag()
            self.ctrl.dragging = True
            self.ctrl.drag_start_line = ln
            self.ctrl.drag_start_col = col
        else:
            # Normal selection drag - starts new selection
            self.ctrl.start_drag(ln, col)
        
        # Clear word selection mode only if this is a single-click drag
        # (click_count will be 1 for single-click, 2+ for multi-click)
        if self.click_count <= 1:
            self.word_selection_mode = False
        
        self.queue_draw()




    def xy_to_line_col(self, x, y):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())
        ln = self.scroll_line + int(y // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        text = self.buf.get_line(ln)

        # Create layout for RTL/LTR measurement
        layout = self.create_text_layout(cr, text if text else " ")

        # Detect RTL exactly as draw()
        rtl = detect_rtl_line(text)
        text_w, _ = layout.get_pixel_size()
        view_w = self.get_width()

        base_x = self.renderer.calculate_text_base_x(rtl, text_w, view_w, ln_width, self.scroll_x)

        # Calculate pixel position relative to text start
        col_px = x - base_x
        
        # For RTL: check if clicking in the newline area (left of text)
        # For LTR: check if clicking in the newline area (right of text)
        if rtl:
            # RTL text: newline area is to the LEFT of the text (negative col_px)
            if col_px < 0:
                # Clicked in newline area - return special value len(text)+1
                return ln, len(text) + 1
        else:
            # LTR text: newline area is to the RIGHT of the text
            if col_px >= text_w:
                # Clicked in newline area - return special value len(text)+1
                return ln, len(text) + 1
        
        col_px = max(0, col_px)
        col = self.pixel_to_column(cr, text, col_px)
        col = max(0, min(col, len(text)))

        return ln, col

    def on_drag_update(self, g, dx, dy):
        ok, sx, sy = g.get_start_point()
        if not ok:
            return

        # Check if we have a pending drag that needs to be activated
        if self._drag_pending:
            # We moved! Activate drag-and-drop mode
            self.drag_and_drop_mode = True
            self._drag_pending = False
            # Now we know it's a drag, so it's NOT a click-to-clear
            self._clicked_in_selection = False
            self.queue_draw()

        # In drag-and-drop mode, track drop position for visual feedback
        if self.drag_and_drop_mode:
            drop_ln, drop_col = self.xy_to_line_col(sx + dx, sy + dy)
            self.drop_position_line = drop_ln
            self.drop_position_col = drop_col
            
            # Check if Ctrl is pressed for copy vs move visual feedback
            event = g.get_current_event()
            if event:
                state = event.get_modifier_state()
                self.ctrl_pressed_during_drag = (state & Gdk.ModifierType.CONTROL_MASK) != 0
            
            self.queue_draw()
            return

        ln, col = self.xy_to_line_col(sx + dx, sy + dy)
        
        if self.word_selection_mode:
            # Word-by-word selection mode
            line_text = self.buf.get_line(ln)
            
            # Get selection anchor point (start of initially selected word)
            sel_start_line = self.buf.selection.start_line if self.buf.selection.active else ln
            sel_start_col = self.buf.selection.start_col if self.buf.selection.active else col
            
            # Also track the end of original selection to determine drag direction
            sel_end_line = self.buf.selection.end_line if self.buf.selection.active else ln
            sel_end_col = self.buf.selection.end_col if self.buf.selection.active else col
            
            # Handle empty lines
            if len(line_text) == 0:
                # Empty line - check if it's the last line (skip it)
                if ln == self.buf.total() - 1:
                    # Last empty line: don't extend to it, stay at previous position
                    return
                else:
                    # Empty line not at EOF: treat entire line as one "word"
                    # Use start or end based on direction
                    if ln > sel_end_line or (ln == sel_end_line and 0 >= sel_end_col):
                        # Dragging forward from end of selection
                        self.ctrl.update_drag(ln, 0)
                    else:
                        # Dragging backward from start of selection
                        self.ctrl.update_drag(ln, 0)
            elif line_text and 0 <= col <= len(line_text):
                # Line with text: snap to word boundaries
                start_col, end_col = self.find_word_boundaries(line_text, min(col, len(line_text) - 1))
                
                # Use the ANCHOR word (originally double-clicked word) for direction detection
                # This prevents flickering by keeping the reference point stable
                anchor_start_line = self.anchor_word_start_line
                anchor_start_col = self.anchor_word_start_col
                anchor_end_line = self.anchor_word_end_line
                anchor_end_col = self.anchor_word_end_col
                
                # Compare current position with anchor word start to determine direction
                # If we are at or after the start of the anchor word, we treat it as a forward drag
                # (even if we are inside the anchor word itself)
                is_forward = False
                if ln > anchor_start_line:
                    is_forward = True
                elif ln == anchor_start_line and col >= anchor_start_col:
                    is_forward = True
                
                if is_forward:
                    # Dragging Forward (LTR):
                    # Anchor point should be the START of the original word
                    self.buf.selection.set_start(anchor_start_line, anchor_start_col)
                    # Cursor (end point) should be the END of the current word
                    self.ctrl.update_drag(ln, end_col)
                else:
                    # Dragging Backward (RTL):
                    # Anchor point should be the END of the original word
                    self.buf.selection.set_start(anchor_end_line, anchor_end_col)
                    # Cursor (end point) should be the START of the current word
                    self.ctrl.update_drag(ln, start_col)
            else:
                # Beyond text
                self.ctrl.update_drag(ln, col)
        else:
            # Normal character-by-character selection
            self.ctrl.update_drag(ln, col)
        
        self.queue_draw()


    def on_click_released(self, g, n, x, y):
        if self._pending_click:
            self.ctrl.click(self._click_ln, self._click_col)
        self._pending_click = False
        self.queue_draw()

    def on_drag_end(self, g, dx, dy):
        # If we clicked in selection but didn't actually drag (drag_and_drop_mode wasn't set),
        # then we should clear the selection now
        if self._clicked_in_selection and not self.drag_and_drop_mode:
            self.buf.selection.clear()
            self._clicked_in_selection = False
            self.queue_draw()
            
        self._drag_pending = False
        
        if self.drag_and_drop_mode:
            # Drag-and-drop mode: move or copy text
            ok, sx, sy = g.get_start_point()
            if ok:
                drop_ln, drop_col = self.xy_to_line_col(sx + dx, sy + dy)
                
                # Get current event to check for Ctrl key
                event = g.get_current_event()
                ctrl_pressed = False
                if event:
                    state = event.get_modifier_state()
                    ctrl_pressed = (state & Gdk.ModifierType.CONTROL_MASK) != 0
                
                # Get original selection bounds
                bounds = self.buf.selection.get_bounds()
                if not bounds or bounds[0] is None:
                    # No valid selection, exit drag mode
                    self.drag_and_drop_mode = False
                    self.dragged_text = ""
                    self.queue_draw()
                    return
                
                start_line, start_col, end_line, end_col = bounds
                
                # Check if dropping inside the original selection (no-op)
                drop_in_selection = False
                if start_line == end_line:
                    if drop_ln == start_line and start_col <= drop_col <= end_col:
                        drop_in_selection = True
                else:
                    if drop_ln == start_line and drop_col >= start_col:
                        drop_in_selection = True
                    elif drop_ln == end_line and drop_col <= end_col:
                        drop_in_selection = True
                    elif start_line < drop_ln < end_line:
                        drop_in_selection = True
                
                if not drop_in_selection and self.dragged_text:
                    if ctrl_pressed:
                        # Copy: insert at drop position, keep original
                        self.buf.set_cursor(drop_ln, drop_col)
                        self.buf.insert_text(self.dragged_text)
                    else:
                        # Move: delete original, insert at drop position
                        # Delete first
                        self.buf.delete_selection()
                        # Recalculate drop position if it's after the deleted text
                        if drop_ln > end_line or (drop_ln == end_line and drop_col > end_col):
                            # Adjust for deleted text
                            if start_line == end_line:
                                # Single line deletion
                                chars_deleted = end_col - start_col
                                if drop_ln == start_line:
                                    drop_col -= chars_deleted
                            else:
                                # Multi-line deletion
                                lines_deleted = end_line - start_line
                                if drop_ln > end_line:
                                    drop_ln -= lines_deleted
                        
                        # Insert at adjusted position
                        self.buf.set_cursor(drop_ln, drop_col)
                        self.buf.insert_text(self.dragged_text)
                
                self.keep_cursor_visible()
            
            # Exit drag-and-drop mode
            self.drag_and_drop_mode = False
            self.dragged_text = ""
        else:
            # Normal drag end
            self.ctrl.end_drag()
            
            # Copy selection to PRIMARY clipboard for middle-click paste
            if self.buf.selection.has_selection():
                start_ln, start_col, end_ln, end_col = self.buf.selection.get_bounds()
                
                # Extract selected text
                if start_ln == end_ln:
                    # Single line selection
                    line = self.buf.get_line(start_ln)
                    selected_text = line[start_col:end_col]
                else:
                    # Multi-line selection
                    lines = []
                    for ln in range(start_ln, end_ln + 1):
                        line = self.buf.get_line(ln)
                        if ln == start_ln:
                            lines.append(line[start_col:])
                        elif ln == end_ln:
                            lines.append(line[:end_col])
                        else:
                            lines.append(line)
                    selected_text = '\n'.join(lines)
                
                # Copy to PRIMARY clipboard
                if selected_text:
                    display = self.get_display()
                    clipboard = display.get_primary_clipboard()
                    clipboard.set(selected_text)
        
        # Clear word selection mode
        self.word_selection_mode = False
        
        self.queue_draw()


    def keep_cursor_visible(self):
        """Smooth, non-jumping cursor tracking for horizontal and vertical scroll."""
        cl = self.buf.cursor_line
        cc = self.buf.cursor_col

        alloc_w = self.get_width()
        alloc_h = self.get_height()
        if alloc_w <= 0 or alloc_h <= 0:
            return

        # ----- compute line height window -----
        line_h = self.renderer.line_h
        visible_lines = alloc_h // line_h

        # Vertical auto-scroll
        if cl < self.scroll_line:
            self.scroll_line = cl
            self.vadj.set_value(self.scroll_line)
        elif cl >= self.scroll_line + visible_lines:
            self.scroll_line = cl - visible_lines + 1
            if self.scroll_line < 0:
                self.scroll_line = 0
            self.vadj.set_value(self.scroll_line)

        # ----- compute cursor X inside renderer -----
        line_text = self.buf.get_line(cl)

        # Build Pango layout to get exact pixel position
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        layout = self.create_text_layout(cr, line_text if line_text else " ")

        # RTL detection (mirrors renderer.draw)
        rtl = detect_rtl_line(line_text)
        byte_index = self.visual_byte_index(line_text, cc)
        strong_pos, weak_pos = layout.get_cursor_pos(byte_index)
        cursor_px = strong_pos.x // Pango.SCALE
        ln_w = self.renderer.calculate_line_number_width(cr, self.buf.total())
        alloc_w = self.get_width()

        # Calculate base X exactly as renderer.draw does
        text_w, _ = layout.get_pixel_size()
        base_x = self.renderer.calculate_text_base_x(rtl, text_w, alloc_w, ln_w, self.scroll_x)

        cursor_screen_x = base_x + cursor_px

        # ----- Horizontal auto-scroll (NON-JUMPING FIX) -----
        # Add a 2px comfort margin
        left_margin = ln_w + 2
        right_margin = alloc_w - 2

        if cursor_screen_x < left_margin:
            # Smooth left scroll
            self.scroll_x -= (left_margin - cursor_screen_x)
            if self.scroll_x < 0:
                self.scroll_x = 0
            self.hadj.set_value(self.scroll_x)

        elif cursor_screen_x > right_margin:
            # Smooth right scroll
            self.scroll_x += (cursor_screen_x - right_margin)
            max_hscroll = max(0, self.renderer.max_line_width - alloc_w)
            if self.scroll_x > max_hscroll:
                self.scroll_x = max_hscroll
            self.hadj.set_value(self.scroll_x)



    def install_scroll(self):
        sc = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, c, dx, dy):
        total = self.buf.total()
        max_vis = max(1, self.get_height() // self.renderer.line_h)
        max_scroll = max(0, total - max_vis)


        if dy:
            self.scroll_line = max(
                0,
                min(self.scroll_line + int(dy * 4), max_scroll)
            )

        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

        self.update_scrollbar()


        self.queue_draw()
        return True


    def draw_view(self, area, cr, w, h):
        cr.set_source_rgb(0.10, 0.10, 0.10)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        alloc = type("Alloc", (), {"width": w, "height": h})

        # Hide cursor if there's an active selection
        show_cursor = self.cursor_visible and not self.buf.selection.has_selection()

        self.renderer.draw(
            cr,
            alloc,
            self.buf,
            self.scroll_line,
            self.scroll_x,
            show_cursor,
            self.cursor_phase   # NEW
        )
        # Update scrollbars after drawing (this updates visibility based on content)
        #GLib.idle_add(lambda: (self.update_scrollbar(), False))









# ============================================================
#   LOADING DIALOG
# ============================================================

class LoadingDialog(Adw.Window):
    def __init__(self, parent):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(300, 150)
        self.set_title("Loading File")
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        
        self.label = Gtk.Label(label="Indexing file...")
        box.append(self.label)
        
        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        box.append(self.progress)
        
        spinner = Gtk.Spinner()
        spinner.start()
        box.append(spinner)
        
        self.set_content(box)
    
    def update_progress(self, fraction):
        """Update progress bar (must be called from main thread)"""
        self.progress.set_fraction(fraction)
        self.progress.set_text(f"{int(fraction * 100)}%")


# ============================================================
#   WINDOW
# ============================================================

class EditorWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Virtual Text Editor")
        self.set_default_size(320, 240)

        self.buf = VirtualBuffer()
        self.view = VirtualTextView(self.buf)
        self.vscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL, adjustment=self.view.vadj)
        self.hscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.view.hadj)

        self.vscroll.add_css_class("overlay-scrollbar")
        self.hscroll.add_css_class("hscrollbar-overlay")
        self.vscroll.set_visible(False)
        self.hscroll.set_visible(False)


        # IMPORTANT: give the view references to both scrollbars
        self.view.vscroll = self.vscroll
        self.view.hscroll = self.hscroll

        self.buf.connect("changed", self.on_buffer_changed)

        layout = Adw.ToolbarView()
        self.set_content(layout)

        header = Adw.HeaderBar()
        layout.add_top_bar(header)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)

        # Clean GTK4 layout: scrollbars OUTSIDE the text viewport
        grid = Gtk.Grid()
        grid.set_column_spacing(0)
        grid.set_row_spacing(0)

        # Text view occupies top-left cell
        grid.attach(self.view, 0, 0, 1, 1)

        # Vertical scrollbar on right
        self.vscroll.set_hexpand(False)
        self.vscroll.set_vexpand(True)
        grid.attach(self.vscroll, 1, 0, 1, 1)

        # Horizontal scrollbar at bottom
        self.hscroll.set_hexpand(True)
        self.hscroll.set_vexpand(False)
        grid.attach(self.hscroll, 0, 1, 1, 1)

        # Corner filler (bottom-right)
        corner = Gtk.Box()
        corner.set_size_request(12, 12)
        grid.attach(corner, 1, 1, 1, 1)
        # Match viewport/editor background
        grid.set_css_classes(["editor-surface"])
        # Put grid into main window
        layout.set_content(grid)


    def on_buffer_changed(self, *_):
        self.view.queue_draw()

        width = self.view.get_width()
        height = self.view.get_height()

        if width <= 0 or height <= 0:
            GLib.idle_add(self.on_buffer_changed)
            return

        total = self.buf.total()
        line_h = self.view.renderer.line_h
        visible = max(1, height // line_h)

        # vertical
        vadj = self.view.vadj
        vadj.set_lower(0)
        vadj.set_upper(total)
        vadj.set_page_size(visible)
        vadj.set_step_increment(1)
        vadj.set_page_increment(visible)

        # clamp
        max_scroll = max(0, total - visible)
        if self.view.scroll_line > max_scroll:
            self.view.scroll_line = max_scroll
            vadj.set_value(max_scroll)

        # horizontal
        doc_w = self.view.renderer.max_line_width
        hadj = self.view.hadj

        hadj.set_lower(0)
        hadj.set_upper(doc_w)
        hadj.set_page_size(width)
        hadj.set_step_increment(20)
        hadj.set_page_increment(width // 2)

        max_hscroll = max(0, doc_w - width)
        if self.view.scroll_x > max_hscroll:
            self.view.scroll_x = max_hscroll
            hadj.set_value(max_hscroll)

        # scrollbar show/hide
        self.vscroll.set_visible(total > visible)
        self.hscroll.set_visible(doc_w > width)

    def open_file(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
            except:
                return
            path = f.get_path()
            
            loading_dialog = LoadingDialog(self)
            loading_dialog.present()
            
            idx = IndexedFile(path)
            
            def progress_callback(fraction):
                loading_dialog.update_progress(fraction)
                return False
            
            def index_complete():
                self.buf.load(idx)

                self.view.scroll_line = 0
                self.view.scroll_x = 0
                
                # Trigger width scan for the new file
                self.view.file_loaded()

                # update scrollbars after loading new file
                #GLib.idle_add(lambda: (self.hscroll.update_visibility(),
                 #      self.vscroll.update_visibility(),
                  #     False))


                self.view.queue_draw()
                self.vscroll.queue_draw()
                self.hscroll.queue_draw()

                self.set_title(os.path.basename(path))
                loading_dialog.close()
                return False

            def index_in_thread():
                try:
                    idx.index_file(progress_callback)
                    GLib.idle_add(index_complete)
                except Exception as e:
                    print(f"Error indexing file: {e}")
                    GLib.idle_add(loading_dialog.close)
            
            thread = Thread(target=index_in_thread)
            thread.daemon = True
            thread.start()

        dialog.open(self, None, done)


# ============================================================
#   APP
# ============================================================

class VirtualTextEditor(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.fastrizwaan.vted")

    def do_activate(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS_OVERLAY_SCROLLBAR)

        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    VirtualTextEditor().run(sys.argv)
