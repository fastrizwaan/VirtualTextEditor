import sys
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Pango, PangoCairo
import cairo

# Mock the vted module by importing it
import vted
from vted import Renderer, VirtualBuffer, BiDiUtils

# Mock Buffer
class MockBuffer:
    def __init__(self):
        self.lines = [
            "فلكج سادفكج اسدفل", # RTL Line 1
            "سدفل",             # RTL Line 2 (Short)
            "لكاجسدف اسد",      # RTL Line 3 (Medium)
        ]
        self.cursor_line = 0
        self.cursor_col = 0
        self.selection = type("Sel", (), {"has_selection": lambda: False})()
        self.preedit_string = ""

    def get_line(self, ln):
        if 0 <= ln < len(self.lines):
            return self.lines[ln]
        return ""
    
    def total(self):
        return len(self.lines)

# Setup
renderer = Renderer()
buf = MockBuffer()
surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 800, 600)
cr = cairo.Context(surface)
alloc = type("Alloc", (), {"width": 800, "height": 600})

# Test Draw
print("--- Testing RTL Mode = True ---")
rtl_mode = True
scroll_x = 0
max_scroll = 0 # Not used in new formula

# We can't easily call renderer.draw because it does a lot of UI stuff.
# But we can test calculate_base_x directly using the logic from draw.

ln_width = 50
view_w = 800
available = view_w - ln_width

for i, line in enumerate(buf.lines):
    is_rtl = BiDiUtils.is_rtl_line(line)
    
    # Measure text width (approximate since we don't have full Pango setup easily without main loop)
    # But we can use PangoCairo if we have a context
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(renderer.font)
    layout.set_text(line, -1)
    text_w, _ = layout.get_pixel_size()
    
    base_x = renderer.calculate_base_x(is_rtl, text_w, view_w, ln_width, scroll_x, max_scroll, rtl_mode)
    
    print(f"Line {i}: '{line}'")
    print(f"  is_rtl: {is_rtl}")
    print(f"  text_w: {text_w}")
    print(f"  base_x: {base_x}")
    print(f"  Right Edge (base_x + text_w): {base_x + text_w}")

print("\n--- Testing RTL Mode = False ---")
rtl_mode = False
for i, line in enumerate(buf.lines):
    is_rtl = BiDiUtils.is_rtl_line(line)
    layout.set_text(line, -1)
    text_w, _ = layout.get_pixel_size()
    base_x = renderer.calculate_base_x(is_rtl, text_w, view_w, ln_width, scroll_x, max_scroll, rtl_mode)
    print(f"Line {i}: Right Edge: {base_x + text_w}")
