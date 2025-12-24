#!/usr/bin/env python3
"""
Self-contained Selection Feature Module

This module provides selection capabilities that can be integrated into any text
buffer class. It's designed to be:
- Self-contained: All dependencies are minimal (Python stdlib only)
- Reusable: Can be used with VirtualBuffer, LineIndexer, or any buffer class
- Optional: The main application works without this module

Usage:
    from selection_feature import SelectionManager, Selection
    
    # Create selection for your buffer
    buffer = MyBuffer()  # Any buffer with get_line(), total() methods
    selection = Selection()
    manager = SelectionManager(buffer, selection)
    
    # Use selection operations
    manager.move_cursor_up(with_selection=True)  # Shift+Up
    manager.select_all()  # Ctrl+A
    text = manager.get_selected_text()
"""

from typing import Protocol, Tuple, Optional


# ============================================================
#   PROTOCOL DEFINITIONS
# ============================================================

class SelectableBuffer(Protocol):
    """Protocol defining the interface required for selection operations"""
    
    cursor_line: int
    cursor_col: int
    
    def total(self) -> int:
        """Return total number of lines in buffer"""
        ...
    
    def get_line(self, line_num: int) -> str:
        """Get text content of a specific line"""
        ...


# ============================================================
#   SELECTION CLASS
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
    
    def clear(self) -> None:
        """Clear the selection"""
        self.start_line = -1
        self.start_col = -1
        self.end_line = -1
        self.end_col = -1
        self.active = False
        self.selecting_with_keyboard = False
    
    def set_start(self, line: int, col: int) -> None:
        """Set selection start point"""
        self.start_line = line
        self.start_col = col
        self.end_line = line
        self.end_col = col
        self.active = True
    
    def set_end(self, line: int, col: int) -> None:
        """Set selection end point"""
        self.end_line = line
        self.end_col = col
        self.active = (self.start_line != self.end_line or self.start_col != self.end_col)
    
    def has_selection(self) -> bool:
        """Check if there's an active selection"""
        return self.active and (
            self.start_line != self.end_line or 
            self.start_col != self.end_col
        )
    
    def get_bounds(self) -> Tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
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
    
    def contains_position(self, line: int, col: int) -> bool:
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
#   SELECTION MANAGER
# ============================================================

class SelectionManager:
    """Manages selection operations for any buffer"""
    
    def __init__(self, buffer: SelectableBuffer, selection: Selection):
        self.buffer = buffer
        self.selection = selection
    
    # --------------------------------------------------------
    # Cursor Movement with Selection
    # --------------------------------------------------------
    
    def move_cursor_up(self, with_selection: bool = False) -> None:
        """Move cursor up one line, optionally extending selection"""
        if with_selection:
            if not self.selection.active:
                self.selection.set_start(self.buffer.cursor_line, self.buffer.cursor_col)
                self.selection.selecting_with_keyboard = True
        else:
            self.selection.clear()
        
        if self.buffer.cursor_line > 0:
            self.buffer.cursor_line -= 1
            # Clamp column to line length
            line_text = self.buffer.get_line(self.buffer.cursor_line)
            self.buffer.cursor_col = min(self.buffer.cursor_col, len(line_text))
        
        if with_selection:
            self.selection.set_end(self.buffer.cursor_line, self.buffer.cursor_col)
    
    def move_cursor_down(self, with_selection: bool = False) -> None:
        """Move cursor down one line, optionally extending selection"""
        if with_selection:
            if not self.selection.active:
                self.selection.set_start(self.buffer.cursor_line, self.buffer.cursor_col)
                self.selection.selecting_with_keyboard = True
        else:
            self.selection.clear()
        
        if self.buffer.cursor_line < self.buffer.total() - 1:
            self.buffer.cursor_line += 1
            # Clamp column to line length
            line_text = self.buffer.get_line(self.buffer.cursor_line)
            self.buffer.cursor_col = min(self.buffer.cursor_col, len(line_text))
        
        if with_selection:
            self.selection.set_end(self.buffer.cursor_line, self.buffer.cursor_col)
    
    def move_cursor_left(self, with_selection: bool = False) -> None:
        """Move cursor left one character, optionally extending selection"""
        if with_selection:
            if not self.selection.active:
                self.selection.set_start(self.buffer.cursor_line, self.buffer.cursor_col)
                self.selection.selecting_with_keyboard = True
        else:
            self.selection.clear()
        
        if self.buffer.cursor_col > 0:
            self.buffer.cursor_col -= 1
        elif self.buffer.cursor_line > 0:
            # Move to end of previous line
            self.buffer.cursor_line -= 1
            line_text = self.buffer.get_line(self.buffer.cursor_line)
            self.buffer.cursor_col = len(line_text)
        
        if with_selection:
            self.selection.set_end(self.buffer.cursor_line, self.buffer.cursor_col)
    
    def move_cursor_right(self, with_selection: bool = False) -> None:
        """Move cursor right one character, optionally extending selection"""
        if with_selection:
            if not self.selection.active:
                self.selection.set_start(self.buffer.cursor_line, self.buffer.cursor_col)
                self.selection.selecting_with_keyboard = True
        else:
            self.selection.clear()
        
        line_text = self.buffer.get_line(self.buffer.cursor_line)
        if self.buffer.cursor_col < len(line_text):
            self.buffer.cursor_col += 1
        elif self.buffer.cursor_line < self.buffer.total() - 1:
            # Move to start of next line
            self.buffer.cursor_line += 1
            self.buffer.cursor_col = 0
        
        if with_selection:
            self.selection.set_end(self.buffer.cursor_line, self.buffer.cursor_col)
    
    def move_to_line_start(self, with_selection: bool = False) -> None:
        """Move cursor to start of line (Home key)"""
        if with_selection:
            if not self.selection.active:
                self.selection.set_start(self.buffer.cursor_line, self.buffer.cursor_col)
                self.selection.selecting_with_keyboard = True
        else:
            self.selection.clear()
        
        self.buffer.cursor_col = 0
        
        if with_selection:
            self.selection.set_end(self.buffer.cursor_line, self.buffer.cursor_col)
    
    def move_to_line_end(self, with_selection: bool = False) -> None:
        """Move cursor to end of line (End key)"""
        if with_selection:
            if not self.selection.active:
                self.selection.set_start(self.buffer.cursor_line, self.buffer.cursor_col)
                self.selection.selecting_with_keyboard = True
        else:
            self.selection.clear()
        
        line_text = self.buffer.get_line(self.buffer.cursor_line)
        self.buffer.cursor_col = len(line_text)
        
        if with_selection:
            self.selection.set_end(self.buffer.cursor_line, self.buffer.cursor_col)
    
    # --------------------------------------------------------
    # Selection Operations
    # --------------------------------------------------------
    
    def select_all(self) -> None:
        """Select all text in buffer (Ctrl+A)"""
        if self.buffer.total() == 0:
            return
        
        self.selection.set_start(0, 0)
        last_line = self.buffer.total() - 1
        last_line_text = self.buffer.get_line(last_line)
        self.selection.set_end(last_line, len(last_line_text))
        self.selection.active = True
    
    def select_line(self, line_num: Optional[int] = None) -> None:
        """Select entire line (current line if not specified)"""
        if line_num is None:
            line_num = self.buffer.cursor_line
        
        if 0 <= line_num < self.buffer.total():
            self.selection.set_start(line_num, 0)
            line_text = self.buffer.get_line(line_num)
            self.selection.set_end(line_num, len(line_text))
            self.selection.active = True
    
    def select_word_at_cursor(self) -> None:
        """Select word at current cursor position"""
        line_text = self.buffer.get_line(self.buffer.cursor_line)
        if not line_text or self.buffer.cursor_col >= len(line_text):
            return
        
        # Find word boundaries
        start_col = self.buffer.cursor_col
        end_col = self.buffer.cursor_col
        
        # Expand left to word start
        while start_col > 0 and (line_text[start_col - 1].isalnum() or line_text[start_col - 1] == '_'):
            start_col -= 1
        
        # Expand right to word end
        while end_col < len(line_text) and (line_text[end_col].isalnum() or line_text[end_col] == '_'):
            end_col += 1
        
        self.selection.set_start(self.buffer.cursor_line, start_col)
        self.selection.set_end(self.buffer.cursor_line, end_col)
        self.selection.active = True
    
    def get_selected_text(self) -> str:
        """Get the currently selected text"""
        if not self.selection.has_selection():
            return ""
        
        start_line, start_col, end_line, end_col = self.selection.get_bounds()
        
        if start_line == end_line:
            # Single line selection
            line_text = self.buffer.get_line(start_line)
            return line_text[start_col:end_col]
        else:
            # Multi-line selection
            lines = []
            for i in range(start_line, end_line + 1):
                line_text = self.buffer.get_line(i)
                if i == start_line:
                    lines.append(line_text[start_col:])
                elif i == end_line:
                    lines.append(line_text[:end_col])
                else:
                    lines.append(line_text)
            return '\n'.join(lines)
    
    def delete_selection(self) -> str:
        """
        Delete selected text and return it.
        Note: This requires the buffer to have delete/edit capabilities.
        For read-only buffers, just return the text.
        """
        text = self.get_selected_text()
        self.selection.clear()
        return text


# ============================================================
#   INSTALLATION / INTEGRATION
# ============================================================

def has_selection_support() -> bool:
    """Check if all dependencies are available for selection feature"""
    # This module has no external dependencies beyond Python stdlib
    return True


def install_selection_feature(buffer_class: type) -> bool:
    """
    Validate that a buffer class has the required interface for selection.
    
    Args:
        buffer_class: The buffer class to validate
        
    Returns:
        True if the buffer class has the required interface, False otherwise
    """
    required_methods = ['total', 'get_line']
    required_attrs = ['cursor_line', 'cursor_col']
    
    # Check for required methods
    for method in required_methods:
        if not hasattr(buffer_class, method):
            print(f"Warning: Buffer class missing required method: {method}")
            return False
    
    return True


# Export public API
__all__ = [
    'Selection',
    'SelectionManager',
    'install_selection_feature',
    'has_selection_support',
    'SelectableBuffer'
]
