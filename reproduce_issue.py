
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Pango, PangoCairo
import cairo

def test_pango_ranges():
    surface = cairo.ImageSurface(cairo.Format.ARGB32, 200, 200)
    cr = cairo.Context(surface)
    
    layout = PangoCairo.create_layout(cr)
    text = "Hello World This Is A Long Line"
    layout.set_text(text, -1)
    layout.set_width(100 * Pango.SCALE) # Force wrap
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    
    print(f"Text: '{text}'")
    print(f"Layout width: {layout.get_width() / Pango.SCALE}")
    
    iter = layout.get_iter()
    line_idx = 0
    while True:
        line = iter.get_line_readonly()
        l_start = line.start_index
        l_end = l_start + line.length
        
        print(f"Line {line_idx}: bytes {l_start}-{l_end}")
        
        # Test 1: "World" (6-11)
        print("  Test 1: 'World' (6-11)")
        r_start = max(6, l_start)
        r_end = min(11, l_end)
        if r_start < r_end:
            ranges = line.get_x_ranges(r_start, r_end)
            print(f"    Ranges: {ranges}")
            
            # Workaround test
            x1 = line.index_to_x(r_start, False)
            x2 = line.index_to_x(r_end, False)
            print(f"    index_to_x: {x1} - {x2} (width {x2-x1})")

        # Test 2: "Hello" (0-5)
        print("  Test 2: 'Hello' (0-5)")
        r_start = max(0, l_start)
        r_end = min(5, l_end)
        if r_start < r_end:
            ranges = line.get_x_ranges(r_start, r_end)
            print(f"    Ranges: {ranges}")
            x1 = line.index_to_x(r_start, False)
            x2 = line.index_to_x(r_end, False)
            print(f"    index_to_x: {x1} - {x2} (width {x2-x1})")
            
        # Test 3: "Hello World" (0-11)
        print("  Test 3: 'Hello World' (0-11)")
        r_start = max(0, l_start)
        r_end = min(11, l_end)
        if r_start < r_end:
            ranges = line.get_x_ranges(r_start, r_end)
            print(f"    Ranges: {ranges}")
            x1 = line.index_to_x(r_start, False)
            x2 = line.index_to_x(r_end, False)
            print(f"    index_to_x: {x1} - {x2} (width {x2-x1})")
            
        # Continue loop
        if not iter.next_line():
            break
        line_idx += 1
        continue
        
        # Old code below ignored
        sel_start = 6
        sel_end = 11
            

        
        if not iter.next_line():
            break
        line_idx += 1

if __name__ == "__main__":
    test_pango_ranges()
