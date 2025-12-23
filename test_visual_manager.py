
import unittest
import sys
import os
from array import array

# Mock Gtk/Pango for headless testing
class MockLayout:
    def __init__(self, text, width):
        self.text = text
        self.width = width
        self.wrapped_lines = max(1, len(text) // 10) # Mock wrapping every 10 chars
        
    def get_pixel_size(self):
        return (100, self.wrapped_lines * 20) # 20px height per line

class MockRenderer:
    def __init__(self):
        self.line_h = 20

class MockView:
    def __init__(self, buf):
        self.buf = buf
        self.renderer = MockRenderer()
        self.word_wrap = True
        self.visual_manager = None # Will be set by test

class MockBuffer:
    def __init__(self):
        self.lines = ["Line 1", "Line 2 is longer", "Line 3"]
        self.total_lines = len(self.lines)
        self._view = None
    
    def total(self):
        return len(self.lines)
    
    def get_line(self, ln):
        if 0 <= ln < len(self.lines):
            return self.lines[ln]
        return ""

# Import the class to test (we'll paste it here or import if possible, 
# but since it's inside vted.py, we might need to extract it or mock it)
# For this test, I'll copy the VisualLineManager class definition to test its logic directly.

class VisualLineManager:
    CHUNK_SIZE = 1000

    def __init__(self, total_logical_lines=0):
        self.counts = array('B', [1] * total_logical_lines)
        num_chunks = (total_logical_lines + self.CHUNK_SIZE - 1) // self.CHUNK_SIZE
        self.chunk_sums = []
        for i in range(num_chunks):
            start = i * self.CHUNK_SIZE
            end = min(start + self.CHUNK_SIZE, total_logical_lines)
            self.chunk_sums.append(end - start)
        self.total_visual_lines = total_logical_lines

    def update(self, logical_line, visual_count):
        if logical_line < 0 or logical_line >= len(self.counts): return
        visual_count = min(255, max(1, visual_count))
        old_count = self.counts[logical_line]
        if old_count == visual_count: return
        diff = visual_count - old_count
        self.counts[logical_line] = visual_count
        chunk_idx = logical_line // self.CHUNK_SIZE
        if chunk_idx < len(self.chunk_sums):
            self.chunk_sums[chunk_idx] += diff
        self.total_visual_lines += diff

    def insert(self, logical_line, count, visual_count=1):
        visual_count = min(255, max(1, visual_count))
        new_items = array('B', [visual_count] * count)
        if logical_line >= len(self.counts):
            self.counts.extend(new_items)
        else:
            for _ in range(count):
                self.counts.insert(logical_line, visual_count)
        self._rebuild_chunks()

    def delete(self, logical_line, count):
        if logical_line < 0 or count <= 0: return
        end = min(logical_line + count, len(self.counts))
        del self.counts[logical_line:end]
        self._rebuild_chunks()

    def _rebuild_chunks(self):
        total_len = len(self.counts)
        num_chunks = (total_len + self.CHUNK_SIZE - 1) // self.CHUNK_SIZE
        self.chunk_sums = []
        self.total_visual_lines = 0
        for i in range(num_chunks):
            start = i * self.CHUNK_SIZE
            end = min(start + self.CHUNK_SIZE, total_len)
            chunk_sum = sum(self.counts[start:end])
            self.chunk_sums.append(chunk_sum)
            self.total_visual_lines += chunk_sum

    def get_total_visual_lines(self):
        return self.total_visual_lines

    def get_visual_offset(self, logical_line):
        if logical_line <= 0: return 0
        if logical_line >= len(self.counts): return self.total_visual_lines
        visual_offset = 0
        target_chunk = logical_line // self.CHUNK_SIZE
        for i in range(target_chunk):
            visual_offset += self.chunk_sums[i]
        start = target_chunk * self.CHUNK_SIZE
        for i in range(start, logical_line):
            visual_offset += self.counts[i]
        return visual_offset

    def get_logical_position(self, visual_line_index):
        if visual_line_index < 0: return 0, 0
        if visual_line_index >= self.total_visual_lines:
            return len(self.counts) - 1, max(0, self.counts[-1] - 1) if self.counts else 0
        current_visual = 0
        chunk_idx = 0
        for sum_val in self.chunk_sums:
            if current_visual + sum_val > visual_line_index: break
            current_visual += sum_val
            chunk_idx += 1
        logical_line = chunk_idx * self.CHUNK_SIZE
        end_limit = min(logical_line + self.CHUNK_SIZE, len(self.counts))
        while logical_line < end_limit:
            count = self.counts[logical_line]
            if current_visual + count > visual_line_index:
                return logical_line, visual_line_index - current_visual
            current_visual += count
            logical_line += 1
        return len(self.counts) - 1, 0

class TestVisualLineManager(unittest.TestCase):
    def test_init(self):
        vm = VisualLineManager(100)
        self.assertEqual(vm.get_total_visual_lines(), 100)
        self.assertEqual(vm.get_visual_offset(50), 50)

    def test_update(self):
        vm = VisualLineManager(10)
        vm.update(5, 3) # Line 5 now has 3 visual lines
        self.assertEqual(vm.get_total_visual_lines(), 12) # 9 + 3
        self.assertEqual(vm.get_visual_offset(5), 5)
        self.assertEqual(vm.get_visual_offset(6), 8) # 5 + 3

    def test_insert(self):
        vm = VisualLineManager(10)
        vm.update(5, 3)
        vm.insert(5, 2) # Insert 2 lines at index 5
        # Old line 5 becomes line 7
        self.assertEqual(vm.get_total_visual_lines(), 14) # 12 + 2
        self.assertEqual(vm.counts[7], 3) # The wrapped line moved
        self.assertEqual(vm.counts[5], 1) # New line
        self.assertEqual(vm.counts[6], 1) # New line

    def test_delete(self):
        vm = VisualLineManager(10)
        vm.update(5, 3)
        vm.delete(4, 2) # Delete line 4 and 5
        # Line 5 (wrapped) is gone
        self.assertEqual(vm.get_total_visual_lines(), 8) # 10 - 1 - 1 + 0 (since we deleted the wrapped one too)
        # Wait, initial: 1,1,1,1,1,3,1,1,1,1 (total 12)
        # Delete 4, 5: removes 1 and 3.
        # Result: 1,1,1,1,1,1,1,1 (total 8)
        self.assertEqual(len(vm.counts), 8)

    def test_mapping(self):
        vm = VisualLineManager(5)
        vm.update(0, 2) # Line 0: 2 visual
        vm.update(2, 3) # Line 2: 3 visual
        # Lines: [2, 1, 3, 1, 1]
        # Visual indices:
        # L0: 0, 1
        # L1: 2
        # L2: 3, 4, 5
        # L3: 6
        # L4: 7
        
        self.assertEqual(vm.get_logical_position(0), (0, 0))
        self.assertEqual(vm.get_logical_position(1), (0, 1))
        self.assertEqual(vm.get_logical_position(2), (1, 0))
        self.assertEqual(vm.get_logical_position(3), (2, 0))
        self.assertEqual(vm.get_logical_position(5), (2, 2))
        self.assertEqual(vm.get_logical_position(6), (3, 0))

if __name__ == '__main__':
    unittest.main()
