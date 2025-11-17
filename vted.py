#!/usr/bin/env python3
import sys, os, mmap, gi, cairo, time
from threading import Thread
from array import array
import math
import bisect
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, PangoCairo, GLib

CSS_OVERLAY_SCROLLBAR = """
/* Vertical scrollbar */
.vscrollbar-overlay {
    /* Default thickness when content scrolls */
    min-width: 2px;
    margin-right: 0px;
    margin-bottom: 10px;
    padding-top:4px;
    padding-left:5px;
    padding-right:5px;

    background-color: rgba(255, 255, 255, 0.01);
    border-radius: 12px;

    transition:
        min-width 200ms ease,
        background-color 200ms ease,
        border-radius 200ms ease;
}

/* Vertical Hover → wider */
.vscrollbar-overlay:hover {
    min-width: 8px;
    margin-right: 0px;
    background-color: rgba(255, 255, 255, 0.02);
    padding:5px;   
}

/* Vertical Dragging → fully expanded */
.vscrollbar-overlay.drag-active {
    min-width: 8px;
    margin-right: 0px;
    background-color: rgba(255, 255, 255, 0.03);
    padding:5px;
}

/* Horizontal scrollbar */
.hscrollbar-overlay {
    /* Default thickness when content scrolls */
    min-height: 2px;
    margin-bottom: 0px;
    margin-right: 10px;
    padding-left:4px;
    padding-top:5px;
    padding-bottom:5px;

    background-color: rgba(255, 255, 255, 0.01);
    border-radius: 12px;

    transition:
        min-height 200ms ease,
        background-color 200ms ease,
        border-radius 200ms ease;
}

/* Horizontal Hover → taller */
.hscrollbar-overlay:hover {
    min-height: 8px;
    margin-bottom: 0px;
    background-color: rgba(255, 255, 255, 0.02);
    padding:5px;   
}

/* Horizontal Dragging → fully expanded */
.hscrollbar-overlay.drag-active {
    min-height: 8px;
    margin-bottom: 0px;
    background-color: rgba(255, 255, 255, 0.03);
    padding:5px;
}
"""




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
            b = f.read(4)
        if b.startswith(b"\xff\xfe"):
            return "utf-16le"
        if b.startswith(b"\xfe\xff"):
            return "utf-16be"
        if b.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
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
            if not self.selection.active:
                self.selection.set_start(self.cursor_line, self.cursor_col)
            self.selection.set_end(ln, col)
        else:
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
        """Start a drag selection"""
        self.dragging = True
        self.drag_start_line = ln
        self.drag_start_col = col
        self.buf.selection.set_start(ln, col)
        self.buf.selection.set_end(ln, col)
        self.buf.set_cursor(ln, col)

    def update_drag(self, ln, col):
        """Update drag selection"""
        if self.dragging:
            self.buf.selection.set_end(ln, col)
            self.buf.set_cursor(ln, col)

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
            b.set_cursor(ln, col - 1, extend_selection)
        elif ln > 0:
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
            b.set_cursor(ln, col + 1, extend_selection)
        elif ln + 1 < b.total():
            b.set_cursor(ln + 1, 0, extend_selection)

    def move_up(self, extend_selection=False):
        b = self.buf
        ln = b.cursor_line
        if ln > 0:
            target = ln - 1
            line = b.get_line(target)
            b.set_cursor(target, min(b.cursor_col, len(line)), extend_selection)

    def move_down(self, extend_selection=False):
        b = self.buf
        ln = b.cursor_line
        if ln + 1 < b.total():
            target = ln + 1
            line = b.get_line(target)
            b.set_cursor(target, min(b.cursor_col, len(line)), extend_selection)

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
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(self.font)
            layout.set_auto_dir(True)
            
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
            if is_rtl:
                available = max(0, alloc.width - ln_width)
                if scroll_x == 0:
                    base_x = ln_width + max(0, available - text_w)
                else:
                    base_x = ln_width + available - scroll_x
            else:
                base_x = ln_width - scroll_x
            
            # Set clipping region to prevent text from overlapping line numbers
            cr.save()
            cr.rectangle(ln_width, y, alloc.width - ln_width, self.line_h)
            cr.clip()

            # Draw selection background for this line if needed
            if has_selection and sel_start_line <= ln <= sel_end_line:
                # Calculate selection range for this line
                if ln == sel_start_line and ln == sel_end_line:
                    # Selection within single line
                    start_col = sel_start_col
                    end_col = sel_end_col
                elif ln == sel_start_line:
                    # First line of multi-line selection
                    start_col = sel_start_col
                    end_col = len(text)
                elif ln == sel_end_line:
                    # Last line of multi-line selection
                    start_col = 0
                    end_col = sel_end_col
                else:
                    # Middle line - select entire line
                    start_col = 0
                    end_col = len(text)
                
                # Calculate pixel positions for selection
                if text:
                    # Get start position
                    start_byte = visual_byte_index(text, start_col)
                    strong_pos, _ = layout.get_cursor_pos(start_byte)
                    sel_start_x = base_x + (strong_pos.x // Pango.SCALE)
                    
                    # Get end position
                    end_byte = visual_byte_index(text, end_col)
                    strong_pos, _ = layout.get_cursor_pos(end_byte)
                    sel_end_x = base_x + (strong_pos.x // Pango.SCALE)
                else:
                    # Empty line - draw selection from line start
                    sel_start_x = base_x
                    sel_end_x = base_x + self.get_text_width(cr, " ")
                
                # Draw selection rectangle
                cr.set_source_rgba(*self.selection_background_color, 0.7)
                
                if is_rtl:
                    # RTL selection might need to be reversed
                    cr.rectangle(min(sel_start_x, sel_end_x), y, 
                            abs(sel_end_x - sel_start_x), self.line_h)
                else:
                    cr.rectangle(sel_start_x, y, 
                            sel_end_x - sel_start_x, self.line_h)
                cr.fill()

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

            pe_l = PangoCairo.create_layout(cr)
            pe_l.set_font_description(self.font)
            pe_l.set_auto_dir(True)
            pe_l.set_text(line_text if line_text else " ", -1)

            is_rtl = line_is_rtl(line_text)
            text_w, _ = pe_l.get_pixel_size()

            if is_rtl:
                available = max(0, alloc.width - ln_width)
                if scroll_x == 0:
                    base_x = ln_width + max(0, available - text_w)
                else:
                    base_x = ln_width + available - scroll_x
            else:
                base_x = ln_width - scroll_x

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
        # NORMAL CURSOR (strong + weak caret, gedit-style)
        # ============================================================
        if cursor_visible and line_visible:
            line_text = buf.get_line(cl)

            cur_l = PangoCairo.create_layout(cr)
            cur_l.set_font_description(self.font)
            cur_l.set_auto_dir(True)
            cur_l.set_text(line_text if line_text else " ", -1)

            is_rtl = line_is_rtl(line_text)
            text_w, _ = cur_l.get_pixel_size()

            if is_rtl:
                available = max(0, alloc.width - ln_width)
                if scroll_x == 0:
                    base_x = ln_width + max(0, available - text_w)
                else:
                    base_x = ln_width + available - scroll_x
            else:
                base_x = ln_width - scroll_x

            byte_index = visual_byte_index(line_text, cc)
            strong_pos, weak_pos = cur_l.get_cursor_pos(byte_index)

            cx_strong = base_x + strong_pos.x // Pango.SCALE
            cx_weak   = base_x + weak_pos.x   // Pango.SCALE
            cy = (cl - scroll_line) * self.line_h

            opacity = 0.5 + 0.5 * math.cos(cursor_phase * math.pi)
            opacity = max(0.0, min(1.0, opacity))

            cr.set_line_width(1.5)

            # Strong caret
            cr.set_source_rgba(1, 1, 1, opacity)
            cr.move_to(cx_strong + 0.4, cy)
            cr.line_to(cx_strong + 0.4, cy + self.text_h)
            cr.stroke()

            # Weak caret (ghost-carets)
            if weak_pos.x != strong_pos.x:
                cr.set_source_rgba(1, 1, 1, opacity * 0.45)
                cr.move_to(cx_weak + 0.4, cy)
                cr.line_to(cx_weak + 0.4, cy + self.text_h)
                cr.stroke()   



# ============================================================
#   VIEW
# ============================================================

class VirtualTextView(Gtk.DrawingArea):

    def __init__(self, buf):
        super().__init__()
        self.buf = buf
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
        self.install_scroll()

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
        vsb = getattr(self, "vscroll", None)
        if vsb is not None:
            vsb.update_visibility()
            vsb.queue_draw()
        hsb = getattr(self, "hscroll", None)
        if hsb is not None:
            hsb.update_visibility()
            hsb.queue_draw()

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
            
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.renderer.font)
        layout.set_auto_dir(True)
        layout.set_text(text, -1)

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
        """Widget lost focus"""
        self.im.focus_out()

    def update_im_cursor_location(self):
        try:
            import unicodedata

            width  = self.get_width()
            height = self.get_height()
            if width <= 0 or height <= 0:
                return

            cl, cc = self.buf.cursor_line, self.buf.cursor_col
            line_text = self.buf.get_line(cl)

            # Pango layout
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            cr = cairo.Context(surface)

            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(self.renderer.font)
            layout.set_auto_dir(True)
            layout.set_text(line_text if line_text else " ", -1)

            # RTL detection (matches Renderer.draw)
            def line_is_rtl(text):
                for ch in text:
                    t = unicodedata.bidirectional(ch)
                    if t in ("L", "LRE", "LRO"):
                        return False
                    if t in ("R", "AL", "RLE", "RLO"):
                        return True
                return False

            is_rtl = line_is_rtl(line_text)
            text_w, _ = layout.get_pixel_size()
            ln_w = self.renderer.calculate_line_number_width(cr, self.buf.total())

            # base_x matches draw()
            if is_rtl:
                available = max(0, width - ln_w)
                if self.scroll_x == 0:
                    base_x = ln_w + max(0, available - text_w)
                else:
                    base_x = ln_w + available - self.scroll_x
            else:
                base_x = ln_w - self.scroll_x

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
                # Word navigation (simplified - just jumps more)
                for _ in range(5):
                    self.ctrl.move_left(extend_selection=shift_pressed)
            else:
                self.ctrl.move_left(extend_selection=shift_pressed)
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Right":
            if ctrl_pressed:
                # Word navigation (simplified)
                for _ in range(5):
                    self.ctrl.move_right(extend_selection=shift_pressed)
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
        """Paste text from clipboard"""
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
                print(f"Paste error: {e}")
        
        clipboard.read_text_async(None, paste_ready)

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
        g = Gtk.GestureClick()
        g.connect("pressed", self.on_click)
        g.connect("released", self.on_release)
        self.add_controller(g)

        d = Gtk.GestureDrag()
        d.connect("drag-begin", self.on_drag_begin)
        d.connect("drag-update", self.on_drag_update)
        d.connect("drag-end", self.on_drag_end)
        self.add_controller(d)

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
        import unicodedata
        
        text = self.buf.get_line(ln)
        
        # Create layout for this line
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.renderer.font)
        layout.set_auto_dir(True)
        layout.set_text(text if text else " ", -1)
        
        # Determine if RTL
        def line_is_rtl(text):
            for ch in text:
                t = unicodedata.bidirectional(ch)
                if t in ("L", "LRE", "LRO"):
                    return False
                if t in ("R", "AL", "RLE", "RLO"):
                    return True
            return False
        
        is_rtl = line_is_rtl(text)
        text_w, _ = layout.get_pixel_size()
        
        # Calculate base_x matching the renderer
        if is_rtl:
            available = max(0, self.get_width() - ln_width)
            if self.scroll_x == 0:
                base_x = ln_width + max(0, available - text_w)
            else:
                base_x = ln_width + available - self.scroll_x
        else:
            base_x = ln_width - self.scroll_x
        
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
        """Start drag selection"""
        # Calculate position
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())

        ln = self.scroll_line + int(y // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        # Calculate column (similar to on_click)
        import unicodedata
        text = self.buf.get_line(ln)
        
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.renderer.font)
        layout.set_auto_dir(True)
        layout.set_text(text if text else " ", -1)
        
        def line_is_rtl(text):
            for ch in text:
                t = unicodedata.bidirectional(ch)
                if t in ("L", "LRE", "LRO"):
                    return False
                if t in ("R", "AL", "RLE", "RLO"):
                    return True
            return False
        
        is_rtl = line_is_rtl(text)
        text_w, _ = layout.get_pixel_size()
        
        if is_rtl:
            available = max(0, self.get_width() - ln_width)
            if self.scroll_x == 0:
                base_x = ln_width + max(0, available - text_w)
            else:
                base_x = ln_width + available - self.scroll_x
        else:
            base_x = ln_width - self.scroll_x
        
        col_pixels = x - base_x
        col_pixels = max(0, col_pixels)
        col = self.pixel_to_column(cr, text, col_pixels)
        col = max(0, min(col, len(text)))

        self.ctrl.start_drag(ln, col)
        self.queue_draw()

    def on_drag_update(self, g, dx, dy):
        """Update drag selection"""
        ok, sx, sy = g.get_start_point()
        if not ok:
            return

        # Calculate current position
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())

        current_y = sy + dy
        ln = self.scroll_line + int(current_y // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        # Calculate column
        import unicodedata
        text = self.buf.get_line(ln)
        
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.renderer.font)
        layout.set_auto_dir(True)
        layout.set_text(text if text else " ", -1)
        
        def line_is_rtl(text):
            for ch in text:
                t = unicodedata.bidirectional(ch)
                if t in ("L", "LRE", "LRO"):
                    return False
                if t in ("R", "AL", "RLE", "RLO"):
                    return True
            return False
        
        is_rtl = line_is_rtl(text)
        text_w, _ = layout.get_pixel_size()
        
        if is_rtl:
            available = max(0, self.get_width() - ln_width)
            if self.scroll_x == 0:
                base_x = ln_width + max(0, available - text_w)
            else:
                base_x = ln_width + available - self.scroll_x
        else:
            base_x = ln_width - self.scroll_x
        
        current_x = sx + dx
        col_pixels = current_x - base_x
        col_pixels = max(0, col_pixels)
        col = self.pixel_to_column(cr, text, col_pixels)
        col = max(0, min(col, len(text)))

        self.ctrl.update_drag(ln, col)
        self.queue_draw()

    def on_drag_end(self, g, dx, dy):
        """End drag selection"""
        self.ctrl.end_drag()

    def keep_cursor_visible(self):
        import unicodedata

        # vertical scrolling (unchanged)
        max_vis = max(1, self.get_height() // self.renderer.line_h)
        cl = self.buf.cursor_line

        if cl < self.scroll_line:
            self.scroll_line = cl
        elif cl >= self.scroll_line + max_vis:
            self.scroll_line = cl - max_vis + 1

        self.scroll_line = max(0, self.scroll_line)

        # ---- FIXED horizontal scroll for RTL ----
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        line_text = self.buf.get_line(self.buf.cursor_line)

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.renderer.font)
        layout.set_auto_dir(True)
        layout.set_text(line_text if line_text else " ", -1)

        def line_is_rtl(text):
            for ch in text:
                t = unicodedata.bidirectional(ch)
                if t in ("L", "LRE", "LRO"):
                    return False
                if t in ("R", "AL", "RLE", "RLO"):
                    return True
            return False

        is_rtl = line_is_rtl(line_text)
        text_w, _ = layout.get_pixel_size()
        ln_w = self.renderer.calculate_line_number_width(cr, self.buf.total())
        view_w = self.get_width()

        # Get cursor position
        byte_index = self.visual_byte_index(line_text, self.buf.cursor_col)
        strong_pos, weak_pos = layout.get_cursor_pos(byte_index)
        cursor_offset = strong_pos.x // Pango.SCALE

        if is_rtl:
            # For RTL text, we need to handle scrolling differently
            available = max(0, view_w - ln_w)
            
            # Only adjust scroll if cursor is going out of view
            if self.scroll_x == 0 and text_w <= available:
                # Text fits in view, no scrolling needed
                pass
            else:
                # Calculate the actual cursor position on screen
                if self.scroll_x == 0:
                    base_x = ln_w + max(0, available - text_w)
                else:
                    base_x = ln_w + available - self.scroll_x
                
                cursor_x = base_x + cursor_offset
                
                # Adjust scroll only if cursor is out of visible area
                visible_left = ln_w + 20
                visible_right = view_w - 30
                
                if cursor_x < visible_left:
                    # Cursor is too far left, scroll right (decrease scroll_x)
                    delta = visible_left - cursor_x
                    self.scroll_x = max(0, self.scroll_x - delta)
                elif cursor_x > visible_right:
                    # Cursor is too far right, scroll left (increase scroll_x)
                    delta = cursor_x - visible_right
                    self.scroll_x = self.scroll_x + delta
        else:
            # LTR text - standard scrolling
            base_x = ln_w - self.scroll_x
            cursor_x = base_x + cursor_offset

            left = ln_w + 20
            right = view_w - 30

            if cursor_x < left:
                self.scroll_x = max(0, cursor_offset - 20)
            elif cursor_x > right:
                self.scroll_x = cursor_offset - (view_w - ln_w - 50)


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

        self.renderer.draw(
            cr,
            alloc,
            self.buf,
            self.scroll_line,
            self.scroll_x,
            self.cursor_visible,
            self.cursor_phase   # NEW
        )
        # Update scrollbars after drawing (this updates visibility based on content)
        GLib.idle_add(lambda: (self.update_scrollbar(), False))





# ============================================================
#   SCROLLBAR (simple)
# ============================================================

class VirtualVScrollbar(Gtk.DrawingArea):

    def __init__(self, view):
        super().__init__()
        self.view = view

        self.set_halign(Gtk.Align.END)
        self.set_valign(Gtk.Align.FILL)
        self.set_size_request(2, -1)
        self.add_css_class("vscrollbar-overlay")

        self.set_draw_func(self.draw)

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        click.connect("released", self.on_release)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", self.on_drag_end)
        self.add_controller(drag)

        # NEW: hover detection
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self.on_enter)
        motion.connect("leave", self.on_leave)
        self.add_controller(motion)

        self.hovering = False
        self.dragging = False
        self.drag_start_y = 0

    # ---------------------------------------------------------
    # Automatically hide/ show depending on overflow
    # ---------------------------------------------------------

    def update_visibility(self):
        """Hide scrollbar if there is no overflow."""
        total = self.view.buf.total()
        visible = max(1, self.view.get_height() // self.view.renderer.line_h)

        if total <= visible:
            self.set_visible(False)
        else:
            self.set_visible(True)

    # ---------------------------------------------------------
    # Drawing
    # ---------------------------------------------------------

    def on_enter(self, controller, x, y):
        self.hovering = True
        self.queue_draw()

    def on_leave(self, controller):
        self.hovering = False
        self.queue_draw()

    def draw(self, area, cr, w, h):
        if w <= 0 or h <= 0:
            return
        self.update_visibility()
        if not self.get_visible():
            return

        view = self.view
        total = view.buf.total()
        max_vis = max(1, view.get_height() // view.renderer.line_h)
        max_scroll = max(0, total - max_vis)

        if total <= 0:
            thumb_h = h
        else:
            thumb_h = h * (max_vis / total)
            thumb_h = max(30, min(h, thumb_h))

        if max_scroll == 0:
            pos = 0.0
        else:
            pos = view.scroll_line / max_scroll

        pos = max(0, min(1, pos))
        y = pos * (h - thumb_h)

        # NEW: brightness logic using hover + drag flags
        if self.dragging:
            alpha = 0.90
        elif self.hovering:
            alpha = 0.60
        else:
            alpha = 0.50

        cr.set_source_rgba(1, 1, 1, alpha)

        r = min(w, thumb_h) / 2.0
        cr.new_path()
        cr.arc(w - r, y + r,             r, -90 * math.pi/180, 0)
        cr.arc(w - r, y + thumb_h - r,   r, 0, 90 * math.pi/180)
        cr.arc(r,     y + thumb_h - r,   r, 90 * math.pi/180, 180*math.pi/180)
        cr.arc(r,     y + r,             r, 180*math.pi/180, 270*math.pi/180)
        cr.close_path()
        cr.fill()

    # ---------------------------------------------------------
    # Interaction
    # ---------------------------------------------------------

    def on_click(self, g, n_press, x, y):
        if not self.get_visible():
            return
        self.dragging = True
        self.drag_start_y = y
        self.add_css_class("drag-active")

    def on_release(self, g, n_press, x, y):
        if not self.get_visible():
            return
        self.dragging = False
        self.remove_css_class("drag-active")

    def on_drag_begin(self, g, x, y):
        if not self.get_visible():
            return
        self.dragging = True
        self.add_css_class("drag-active")

    def on_drag_end(self, g, dx, dy):
        if not self.get_visible():
            return
        self.dragging = False
        self.remove_css_class("drag-active")

    def on_drag_update(self, g, dx, dy):
        if not self.dragging or not self.get_visible():
            return

        view = self.view
        h = self.get_height()
        total = view.buf.total()

        max_vis = max(1, view.get_height() // view.renderer.line_h)
        max_scroll = max(0, total - max_vis)

        if max_scroll <= 0:
            return

        # Thumb height
        thumb_h = h * (max_vis / total)
        thumb_h = max(20, min(h, thumb_h))

        # Track height
        track = max(1, h - thumb_h)

        frac = (self.drag_start_y + dy) / track
        frac = max(0.0, min(1.0, frac))

        view.scroll_line = int(frac * max_scroll)

        view.queue_draw()
        self.queue_draw()


# ============================================================
#   HORIZONTAL SCROLLBAR (OVERLAY)
# ============================================================

class VirtualHScrollbar(Gtk.DrawingArea):
    """
    Horizontal scrollbar overlay for VirtualTextView
    """

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.set_size_request(-1, 14)
        self.add_css_class("hscrollbar-overlay")

        self.dragging = False
        self.drag_offset = 0      # <-- NEW: offset inside thumb
        self.hovering = False

        self.set_draw_func(self.draw)

        click = Gtk.GestureClick.new()
        click.connect("pressed", self.on_click)
        click.connect("released", self.on_release)
        self.add_controller(click)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-end", self.on_drag_end)
        drag.connect("drag-update", self.on_drag_update)
        self.add_controller(drag)

        hover = Gtk.EventControllerMotion.new()
        hover.connect("enter", lambda *_: setattr(self, 'hovering', True) or self.queue_draw())
        hover.connect("leave", lambda *_: setattr(self, 'hovering', False) or self.queue_draw())
        self.add_controller(hover)

        # Initialize as visible
        self.set_visible(True)

    # ---------------------------------------------------------
    # Helpers needed during drag
    # ---------------------------------------------------------

    def compute_thumb_geometry(self):
        """Return (thumb_x, thumb_w, max_scroll) based on current view."""
        view = self.view
        w = self.get_width()
        view_width = view.get_width()
        max_width = view.renderer.max_line_width + 100

        max_scroll = max(0, max_width - view_width)

        if max_width <= view_width:
            thumb_w = w
        else:
            thumb_w = w * (view_width / max_width)
            thumb_w = max(30, min(w, thumb_w))

        if max_scroll == 0:
            pos = 0.0
        else:
            pos = view.scroll_x / max_scroll

        x = pos * (w - thumb_w)
        return x, thumb_w, max_scroll


    def update_visibility(self):
        """Update scrollbar visibility based on content"""
        if not self.view.buf or not hasattr(self.view.renderer, 'max_line_width'):
            self.set_visible(False)
            return
        
        view_width = self.view.get_width()
        max_width = self.view.renderer.max_line_width + 100  # Add some padding
        
        # Show scrollbar if content is wider than view
        needs_scroll = max_width > view_width
        self.set_visible(needs_scroll)

    # ---------------------------------------------------------
    # Drawing
    # ---------------------------------------------------------

    def draw(self, area, cr, w, h):
        if w <= 0 or h <= 0:
            return
        if not self.view.buf:
            return

        # Calculate thumb dimensions
        view = self.view
        view_width = view.get_width()
        max_width = view.renderer.max_line_width + 100
        
        if max_width <= view_width:
            thumb_w = w
        else:
            thumb_w = w * (view_width / max_width)
            thumb_w = max(30, min(w, thumb_w))

        # Calculate thumb position
        max_scroll = max(0, max_width - view_width)
        if max_scroll == 0:
            pos = 0.0
        else:
            pos = view.scroll_x / max_scroll

        pos = max(0, min(1, pos))
        x = pos * (w - thumb_w)

        # Brightness logic using hover + drag flags
        if self.dragging:
            alpha = 0.90
        elif self.hovering:
            alpha = 0.60
        else:
            alpha = 0.50

        cr.set_source_rgba(1, 1, 1, alpha)

        # Draw rounded rectangle for horizontal scrollbar
        r = min(h, thumb_w) / 2.0
        cr.new_path()
        cr.arc(x + r,             h - r, r, 180 * math.pi/180, 270*math.pi/180)
        cr.arc(x + thumb_w - r,   h - r, r, 270*math.pi/180, 0)
        cr.arc(x + thumb_w - r,   r,     r, 0, 90 * math.pi/180)
        cr.arc(x + r,             r,     r, 90 * math.pi/180, 180*math.pi/180)
        cr.close_path()
        cr.fill()

    # ---------------------------------------------------------
    # Interaction
    # ---------------------------------------------------------

    def on_click(self, g, n_press, x, y):
        if not self.get_visible():
            return

        thumb_x, thumb_w, _ = self.compute_thumb_geometry()

        self.dragging = True
        # Offset pointer inside thumb (fixes jumping)
        self.drag_offset = x - thumb_x
        self.add_css_class("drag-active")

    def on_drag_begin(self, g, x, y):
        if not self.get_visible():
            return

        thumb_x, thumb_w, _ = self.compute_thumb_geometry()

        self.dragging = True
        # Offset pointer inside thumb (fixes jumping)
        self.drag_offset = x - thumb_x
        self.add_css_class("drag-active")

    def on_drag_update(self, g, dx, dy):
        if not self.dragging or not self.get_visible():
            return

        view = self.view
        w = self.get_width()

        thumb_x, thumb_w, max_scroll = self.compute_thumb_geometry()

        if max_scroll <= 0:
            return

        # Track width
        track = max(1, w - thumb_w)

        # Pointer position minus offset inside thumb
        ok, start_x, start_y = g.get_start_point()
        current_x = start_x + dx

        # New thumb-left
        new_thumb_x = current_x - self.drag_offset
        new_thumb_x = max(0.0, min(track, new_thumb_x))

        # Convert thumb position → scroll_x
        frac = new_thumb_x / track
        view.scroll_x = int(frac * max_scroll)

        view.queue_draw()
        self.queue_draw()

    def on_release(self, g, n_press, x, y):
        if not self.get_visible():
            return
        self.dragging = False
        self.remove_css_class("drag-active")

    def on_drag_end(self, g, dx, dy):
        if not self.get_visible():
            return
        self.dragging = False
        self.remove_css_class("drag-active")




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
        self.set_title("Virtual Text Editor - Advanced Selection")
        self.set_default_size(1000, 700)

        self.buf = VirtualBuffer()
        self.view = VirtualTextView(self.buf)
        self.vscroll = VirtualVScrollbar(self.view)
        self.hscroll = VirtualHScrollbar(self.view)

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

        overlay = Gtk.Overlay()

        # main content
        overlay.set_child(self.view)

        # overlay vertical scrollbar
        overlay.add_overlay(self.vscroll)

        # overlay horizontal scrollbar
        overlay.add_overlay(self.hscroll)

        # let vertical scrollbar float on top right
        self.vscroll.set_halign(Gtk.Align.END)
        self.vscroll.set_valign(Gtk.Align.FILL)

        # let horizontal scrollbar float on bottom
        self.hscroll.set_halign(Gtk.Align.FILL)
        self.hscroll.set_valign(Gtk.Align.END)

        layout.set_content(overlay)


    def on_buffer_changed(self, *_):
        self.view.queue_draw()
        self.vscroll.update_visibility()   # auto-hide vertical when content fits
        self.hscroll.update_visibility()   # auto-hide horizontal when content fits
        self.vscroll.queue_draw()
        self.hscroll.queue_draw()


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
                GLib.idle_add(lambda: (self.hscroll.update_visibility(),
                       self.vscroll.update_visibility(),
                       False))


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