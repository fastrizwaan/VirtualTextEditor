#!/usr/bin/env python3
import sys, os, mmap, gi, cairo, time
from threading import Thread
from array import array
import math
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, GObject, Pango, PangoCairo, GLib

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
#   BUFFER
# ============================================================

class VirtualBuffer(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __init__(self):
        super().__init__()
        self.file = None            # IndexedFile
        self.edits = {}             # sparse: line_number → modified string
        self.cursor_line = 0
        self.cursor_col = 0

    def load(self, indexed_file):
        self.file = indexed_file
        self.edits.clear()
        self.cursor_line = 0
        self.cursor_col = 0
        self.emit("changed")

    def total(self):
        """Return total number of logical lines in the buffer.

        If a file is loaded, base it on file length and any edited lines.
        If no file is loaded, base it on edited lines (or at least 1).
        """
        if not self.file:
            # When editing an empty/new buffer, consider edits so added
            if not self.edits:
                return 1
            return max(1, max(self.edits.keys()) + 1)

        # File is present
        if not self.edits:
            return self.file.total_lines()

        max_edited = max(self.edits.keys())
        return max(self.file.total_lines(), max_edited + 1)


    def get_line(self, ln):
        if ln in self.edits:
            return self.edits[ln]
        if self.file:
            return self.file[ln] if 0 <= ln < self.file.total_lines() else ""
        return ""



    def set_cursor(self, ln, col):
        total = self.total()
        ln = max(0, min(ln, total - 1))
        line = self.get_line(ln)
        col = max(0, min(col, len(line)))
        self.cursor_line = ln
        self.cursor_col = col

    # ------- Editing ----------
    def insert_text(self, text):
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col

        new_line = line[:col] + text + line[col:]
        self.edits[ln] = new_line

        self.cursor_col = col + len(text)
        self.emit("changed")

    def backspace(self):
        ln = self.cursor_line
        line = self.get_line(ln)
        col = self.cursor_col

        if col == 0:
            return

        new_line = line[:col-1] + line[col:]
        self.edits[ln] = new_line

        self.cursor_col = col - 1
        self.emit("changed")

    def insert_newline(self):
        ln = self.cursor_line
        col = self.cursor_col

        old_line = self.get_line(ln)
        left = old_line[:col]
        right = old_line[col:]

        # Put the left part into edits (replaces or creates this line)
        self.edits[ln] = left

        # Shift ONLY edited lines that come AFTER ln
        shifted = {}
        for k, v in self.edits.items():
            if k > ln:
                shifted[k + 1] = v
            else:
                shifted[k] = v

        # Insert new blank line or right side of old line
        shifted[ln + 1] = right

        self.edits = shifted

        self.cursor_line = ln + 1
        self.cursor_col = 0
        self.emit("changed")




# ============================================================
#   INPUT
# ============================================================

class InputController:
    def __init__(self, view, buf):
        self.view = view
        self.buf = buf
        self.sel_start = None
        self.sel_end = None

    def click(self, ln, col):
        self.buf.set_cursor(ln, col)
        self.sel_start = (ln, col)
        self.sel_end = (ln, col)

    def drag(self, ln, col):
        self.sel_end = (ln, col)

    def move_left(self):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        if col > 0:
            b.set_cursor(ln, col - 1)
        elif ln > 0:
            prev = b.get_line(ln - 1)
            b.set_cursor(ln - 1, len(prev))

    def move_right(self):
        b = self.buf
        ln, col = b.cursor_line, b.cursor_col
        line = b.get_line(ln)
        if col < len(line):
            b.set_cursor(ln, col + 1)
        elif ln + 1 < b.total():
            b.set_cursor(ln + 1, 0)

    def move_up(self):
        b = self.buf
        ln = b.cursor_line
        if ln > 0:
            target = ln - 1
            line = b.get_line(target)
            b.set_cursor(target, min(b.cursor_col, len(line)))

    def move_down(self):
        b = self.buf
        ln = b.cursor_line
        if ln + 1 < b.total():
            target = ln + 1
            line = b.get_line(target)
            b.set_cursor(target, min(b.cursor_col, len(line)))


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

        # Colors unchanged
        self.editor_background_color = (0.10, 0.10, 0.10)
        self.text_foreground_color   = (0.50, 0.50, 0.50)
        self.linenumber_foreground_color = (0.60, 0.60, 0.60)

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

        # ============================================================
        # DRAW TEXT + LINE NUMBERS
        # ============================================================
        y = 0
        for ln in range(scroll_line, min(scroll_line + max_vis, total)):
            text = buf.get_line(ln)

            # Line number (LTR always)
            layout.set_auto_dir(False)
            layout.set_text(str(ln + 1), -1)
            cr.set_source_rgb(*self.linenumber_foreground_color)
            cr.move_to(5, y)
            PangoCairo.show_layout(cr, layout)

            # Line text
            is_rtl = line_is_rtl(text)
            layout.set_auto_dir(True)
            layout.set_text(text, -1)

            cr.set_source_rgb(*self.text_foreground_color)

            ink, logical = layout.get_pixel_extents()
            text_w = logical.width

            if is_rtl:
                available = max(0, alloc.width - ln_width)
                base_x = ln_width + max(0, available - text_w) - scroll_x
            else:
                base_x = ln_width - scroll_x

            cr.move_to(base_x, y)
            PangoCairo.show_layout(cr, layout)

            y += self.line_h

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
            pe_l.set_text(line_text, -1)

            is_rtl = line_is_rtl(line_text)
            text_w, _ = pe_l.get_pixel_size()

            if is_rtl:
                available = max(0, alloc.width - ln_width)
                base_x = ln_width + max(0, available - text_w) - scroll_x
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
            cur_l.set_text(line_text, -1)

            is_rtl = line_is_rtl(line_text)
            text_w, _ = cur_l.get_pixel_size()

            if is_rtl:
                available = max(0, alloc.width - ln_width)
                base_x = ln_width + max(0, available - text_w) - scroll_x
            else:
                base_x = ln_width - scroll_x

            byte_index = visual_byte_index(line_text, cc)
            strong_pos, weak_pos = cur_l.get_cursor_pos(byte_index)

            cx_strong = base_x + strong_pos.x // Pango.SCALE
            cx_weak   = base_x + weak_pos.x   // Pango.SCALE
            cy = (cl - scroll_line) * self.line_h

            opacity = 0.5 + 0.5 * math.cos(cursor_phase * math.pi)
            opacity = max(0.0, min(1.0, opacity))

            cr.set_line_width(0.8)

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

    # Correct UTF-8 byte-index for logical col → Pango visual mapping
    def visual_byte_index(self, text, col):
        b = 0
        for ch in text[:col]:
            b += len(ch.encode("utf-8"))
        return b

    def pixel_to_column(self, cr, text, px):
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.renderer.font)
        layout.set_auto_dir(True)                   # <── NEW
        layout.set_text(text, -1)

        # Convert to Pango units
        success, index, trailing = layout.xy_to_index(px * Pango.SCALE, 0)
        if not success:
            return len(text)

        # index = byte offset → convert back to UTF-8 column
        substr = text.encode("utf-8")[:index]
        try:
            return len(substr.decode("utf-8"))
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
            layout.set_text(line_text, -1)

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
                base_x = ln_w + max(0, available - text_w) - self.scroll_x
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

        # Editing keys
        if name == "BackSpace":
            self.buf.backspace()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        if name == "Return":
            self.buf.insert_newline()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        # Arrow keys
        if name == "Up":
            self.ctrl.move_up()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Down":
            self.ctrl.move_down()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Left":
            self.ctrl.move_left()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True
        elif name == "Right":
            self.ctrl.move_right()
            self.keep_cursor_visible()
            self.update_im_cursor_location()
            self.queue_draw()
            return True

        return False

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
        self.add_controller(g)

        d = Gtk.GestureDrag()
        d.connect("drag-update", self.on_drag)
        self.add_controller(d)

    def on_click(self, g, n, x, y):
        self.grab_focus()

        # Temporary Pango context
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())

        ln = self.scroll_line + int(y // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        col_pixels = x - ln_width + self.scroll_x
        col_pixels = max(0, col_pixels)

        text = self.buf.get_line(ln)

        # Binary search → column index
        col = self.pixel_to_column(cr, text, col_pixels)


        col = max(0, min(col, len(text)))
        self.ctrl.click(ln, col)
        self.queue_draw()

    def on_drag(self, g, dx, dy):
        ok, sx, sy = g.get_start_point()
        if not ok:
            return

        # Calculate line number width dynamically
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)
        ln_width = self.renderer.calculate_line_number_width(cr, self.buf.total())

        ln = self.scroll_line + int((sy + dy) // self.renderer.line_h)
        ln = max(0, min(ln, self.buf.total() - 1))

        col_pixels = (sx + dx) - ln_width + self.scroll_x
        if col_pixels < 0:
            col = 0
        else:
            line_text = self.buf.get_line(ln)
            # Binary search to find column from pixel position
            col = 0
            for i in range(len(line_text) + 1):
                text_width = self.renderer.get_text_width(cr, line_text[:i])
                if text_width > col_pixels:
                    break
                col = i

        col = max(0, min(col, len(self.buf.get_line(ln))))
        self.ctrl.drag(ln, col)
        self.queue_draw()

    def keep_cursor_visible(self):
        import unicodedata

        # vertical scrolling (unchanged)
        max_vis = max(1, (self.get_height() // self.renderer.line_h) + 1)
        cl = self.buf.cursor_line

        if cl < self.scroll_line:
            self.scroll_line = cl
        elif cl >= self.scroll_line + max_vis:
            self.scroll_line = cl - max_vis + 1

        self.scroll_line = max(0, self.scroll_line)

        # ---- horizontal scroll using correct bidi cursor ----

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        cr = cairo.Context(surface)

        line_text = self.buf.get_line(self.buf.cursor_line)

        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(self.renderer.font)
        layout.set_auto_dir(True)
        layout.set_text(line_text, -1)

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

        if is_rtl:
            available = max(0, view_w - ln_w)
            base_x = ln_w + max(0, available - text_w) - self.scroll_x
        else:
            base_x = ln_w - self.scroll_x

        # ---- FIX: correct UTF-8 → visual cluster byte-index ----
        byte_index = self.visual_byte_index(line_text, self.buf.cursor_col)

        strong_pos, weak_pos = layout.get_cursor_pos(byte_index)
        cursor_offset = strong_pos.x // Pango.SCALE

        cursor_x = base_x + cursor_offset

        left   = self.scroll_x
        right  = self.scroll_x + view_w - 30

        if cursor_x < left:
            self.scroll_x = max(0, cursor_x - 20)
        elif cursor_x > right:
            self.scroll_x = cursor_x - (view_w - 30) + 20


    def install_scroll(self):
        sc = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL |
            Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        sc.connect("scroll", self.on_scroll)
        self.add_controller(sc)

    def on_scroll(self, c, dx, dy):
        total = self.buf.total()
        # Use get_height() instead of get_allocated_height() - GTK4 way
        max_vis = max(1, (self.get_height() // self.renderer.line_h) + 1)
        max_scroll = max(0, total - max_vis)

        if dy:
            self.scroll_line = max(
                0,
                min(self.scroll_line + int(dy * 4), max_scroll)
            )

        if dx:
            self.scroll_x = max(0, self.scroll_x + int(dx * 40))

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



# ============================================================
#   SCROLLBAR (simple)
# ============================================================

class VirtualScrollbar(Gtk.DrawingArea):
    def __init__(self, view):
        super().__init__()
        self.view = view

        self.set_size_request(14, -1)
        self.set_vexpand(True)
        self.set_hexpand(False)

        self.set_draw_func(self.draw_scrollbar)

        click = Gtk.GestureClick()
        click.connect("pressed", self.on_click)
        self.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.connect("drag-update", self.on_drag)
        self.add_controller(drag)

        self.dragging = False

    def draw_scrollbar(self, area, cr, w, h):
        cr.set_source_rgb(0.20, 0.20, 0.20)
        cr.rectangle(0, 0, w, h)
        cr.fill()

        view = self.view
        total = view.buf.total()

        # Visible lines
        max_vis = max(1, (view.get_height() // view.renderer.line_h) + 1)

        # How many lines we can scroll
        max_scroll = max(0, total - max_vis)

        # Thumb height stays the same
        thumb_h = max(20, h * (max_vis / total))

        # ✨ Corrected thumb position math
        if max_scroll <= 0:
            pos = 0.0
        else:
            # Scroll proportion based on remaining scrollable lines
            pos = view.scroll_line / max_scroll

        pos = max(0.0, min(1.0, pos))  # clamp
        y = pos * (h - thumb_h)

        cr.set_source_rgb(0.55, 0.55, 0.55)
        cr.rectangle(0, y, w, thumb_h)
        cr.fill()


    def on_click(self, g, n_press, x, y):
        self.start_y = y
        self.dragging = True

    def on_drag(self, g, dx, dy):
        if not self.dragging:
            return

        view = self.view
        h = self.get_height()  # GTK4: use get_height() instead of get_allocated_height()
        total = view.buf.total()
        max_vis = max(1, view.get_height() // view.renderer.line_h)
        max_scroll = max(0, total - max_vis)

        thumb_h = max(20, h * (max_vis / total))
        track = h - thumb_h
        frac = (self.start_y + dy) / track
        frac = max(0, min(1, frac))

        view.scroll_line = int(frac * max_scroll)
        view.queue_draw()
        self.queue_draw()


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
        self.set_default_size(1000, 700)

        self.buf = VirtualBuffer()
        self.view = VirtualTextView(self.buf)
        self.scrollbar = VirtualScrollbar(self.view)

        layout = Adw.ToolbarView()
        self.set_content(layout)

        header = Adw.HeaderBar()
        layout.add_top_bar(header)

        open_btn = Gtk.Button(label="Open")
        open_btn.connect("clicked", self.open_file)
        header.pack_start(open_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.append(self.view)
        box.append(self.scrollbar)

        layout.set_content(box)

    def open_file(self, *_):
        dialog = Gtk.FileDialog()

        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
            except:
                return
            path = f.get_path()
            
            # Show loading dialog
            loading_dialog = LoadingDialog(self)
            loading_dialog.present()
            
            # Create indexed file
            idx = IndexedFile(path)
            
            def progress_callback(fraction):
                """Update progress (called from worker thread via GLib.idle_add)"""
                loading_dialog.update_progress(fraction)
                return False  # Don't repeat
            
            def index_complete():
                """Called when indexing is done"""
                # Load the indexed file into buffer
                self.buf.load(idx)
                self.view.scroll_line = 0
                self.view.scroll_x = 0
                
                self.view.queue_draw()
                self.scrollbar.queue_draw()
                
                self.set_title(os.path.basename(path))
                
                # Close loading dialog
                loading_dialog.close()
                return False  # Don't repeat
            
            def index_in_thread():
                """Run indexing in background thread"""
                try:
                    idx.index_file(progress_callback)
                    # Schedule completion on main thread
                    GLib.idle_add(index_complete)
                except Exception as e:
                    print(f"Error indexing file: {e}")
                    GLib.idle_add(loading_dialog.close)
            
            # Start indexing in background thread
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
        win = self.props.active_window
        if not win:
            win = EditorWindow(self)
        win.present()


if __name__ == "__main__":
    VirtualTextEditor().run(sys.argv)