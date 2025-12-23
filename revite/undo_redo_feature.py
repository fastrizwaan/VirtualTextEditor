#!/usr/bin/env python3
"""
Self-contained Undo/Redo Feature Module

This module provides a complete undo/redo functionality that can be integrated
into any text editor application. It's designed to be:
- Self-contained: All dependencies are checked and handled gracefully
- Reusable: Can be used in multiple projects
- Optional: The main application works without this module

Usage:
    from undo_redo_feature import install_undo_redo, UndoStack
    
    # Install undo/redo capabilities into your buffer class
    if install_undo_redo(MyBuffer):
        # Feature installed successfully
        pass
"""

import time
from typing import Protocol, Optional, List, Any


# ============================================================
#   PROTOCOL DEFINITIONS
# ============================================================

class SelectionProtocol(Protocol):
    """Protocol defining the interface required for selection operations"""
    
    def set_start(self, line: int, col: int) -> None:
        """Set selection start point"""
        ...
    
    def set_end(self, line: int, col: int) -> None:
        """Set selection end point"""
        ...
    
    def clear(self) -> None:
        """Clear the selection"""
        ...
    
    def get_bounds(self) -> tuple:
        """Get normalized selection bounds (start always before end)"""
        ...
    
    def has_selection(self) -> bool:
        """Check if there's an active selection"""
        ...


class UndoableBuffer(Protocol):
    """Protocol defining the interface required for undo/redo operations"""
    
    cursor_line: int
    cursor_col: int
    selection: SelectionProtocol
    
    def insert_text(self, text: str, _record_undo: bool = True) -> None:
        """Insert text at current cursor position"""
        ...
    
    def delete_selection(self, _record_undo: bool = True) -> None:
        """Delete the current selection"""
        ...
    
    def begin_action(self) -> None:
        """Begin a composite action for undo grouping"""
        ...
    
    def end_action(self) -> None:
        """End a composite action"""
        ...


# ============================================================
#   UNDO COMMAND CLASSES
# ============================================================

class UndoCommand:
    """Abstract base class for undoable commands"""
    
    def undo(self, buffer: UndoableBuffer) -> None:
        """Undo this command"""
        pass
        
    def redo(self, buffer: UndoableBuffer) -> None:
        """Redo this command"""
        pass
        
    def merge(self, other: 'UndoCommand') -> bool:
        """Try to merge with a subsequent command. Returns True if merged."""
        return False


class InsertCommand(UndoCommand):
    """Command for text insertion operations"""
    
    def __init__(self, line: int, col: int, text: str, cursor_after_line: int, 
                 cursor_after_col: int, lines: Optional[List[str]] = None):
        self.line = line
        self.col = col
        self.text = text
        self.lines = lines
        
        # Memory Optimization: If direct lines list provided, don't store full text
        # This allows sharing string objects with the buffer's inserted_lines
        if self.lines and len(self.lines) > 100:
            self.text = None

        self.cursor_after_line = cursor_after_line
        self.cursor_after_col = cursor_after_col
        self.timestamp = time.time()
        
    def undo(self, buffer: UndoableBuffer) -> None:
        """Undo an insertion by deleting the inserted range"""
        # Calculate the end position of the inserted text
        if self.lines:
            lines_count = len(self.lines)
            if lines_count == 1:
                end_line = self.line
                end_col = self.col + len(self.lines[0])
            else:
                end_line = self.line + lines_count - 1
                end_col = len(self.lines[-1])
        else:
            # Fallback for legacy commands or small edits
            text_lines = self.text.split('\n')
            if len(text_lines) == 1:
                end_line = self.line
                end_col = self.col + len(self.text)
            else:
                end_line = self.line + len(text_lines) - 1
                end_col = len(text_lines[-1])
            
        # Select the range
        buffer.selection.set_start(self.line, self.col)
        buffer.selection.set_end(end_line, end_col)
        
        # Delete it (bypassing the undo stack recording)
        buffer.delete_selection(_record_undo=False)
        
        # Restore cursor to start
        buffer.cursor_line = self.line
        buffer.cursor_col = self.col
        
    def redo(self, buffer: UndoableBuffer) -> None:
        """Redo the insertion"""
        buffer.cursor_line = self.line
        buffer.cursor_col = self.col
        buffer.selection.clear()
        
        # Reconstruct text if needed
        if self.text is None and self.lines:
            text_to_insert = '\n'.join(self.lines)
        else:
            text_to_insert = self.text
            
        buffer.insert_text(text_to_insert, _record_undo=False)
        
        # Restore cursor
        buffer.cursor_line = self.cursor_after_line
        buffer.cursor_col = self.cursor_after_col

    def merge(self, other: UndoCommand) -> bool:
        """Merge consecutive typing operations for better undo granularity"""
        if not isinstance(other, InsertCommand):
            return False
            
        # Check if other immediately follows self
        if self.lines:
            if len(self.lines) == 1:
                my_end_line = self.line
                my_end_col = self.col + len(self.lines[0])
            else:
                my_end_line = self.line + len(self.lines) - 1
                my_end_col = len(self.lines[-1])
        else:
            lines = self.text.split('\n')
            if len(lines) == 1:
                my_end_line = self.line
                my_end_col = self.col + len(self.text)
            else:
                my_end_line = self.line + len(lines) - 1
                my_end_col = len(lines[-1])
            
        if other.line != my_end_line or other.col != my_end_col:
            return False
            
        # Check for word grouping logic
        if other.timestamp - self.timestamp > 2.0:
            return False
            
        # If we have complex lines structure (paste), don't merge simple typing
        if self.lines or other.lines:
            return False

        def group_type(txt):
            if not txt: return 0
            if txt.isspace(): return 1  # Whitespace
            if txt.isalnum() or txt == '_': return 2  # Alphanumeric
            return 3  # Punctuation / Symbols
            
        last_group = group_type(self.text[-1])
        new_group = group_type(other.text[0])
        
        if '\n' in self.text:
            return False  # Don't merge across lines for now, safer
            
        if last_group == 1 and new_group != 1:
            return False  # " " -> "a" : Break
            
        # If we have too much text, break
        if len(self.text) > 50:
            return False
             
        self.text += other.text
        self.cursor_after_line = other.cursor_after_line
        self.cursor_after_col = other.cursor_after_col
        self.timestamp = other.timestamp  # Update time
        return True


class DeleteCommand(UndoCommand):
    """Command for text deletion operations"""
    
    def __init__(self, line: int, col: int, text: str, restore_selection: bool = True):
        self.line = line
        self.col = col
        self.text = text
        self.restore_selection = restore_selection
        self.timestamp = time.time()

    def undo(self, buffer: UndoableBuffer) -> None:
        """Undo a deletion by re-inserting the deleted text"""
        buffer.cursor_line = self.line
        buffer.cursor_col = self.col
        buffer.insert_text(self.text, _record_undo=False)

        if self.restore_selection:
            # Calculate range end
            lines = self.text.split('\n')
            if len(lines) == 1:
                end_line = self.line
                end_col = self.col + len(self.text)
            else:
                end_line = self.line + len(lines) - 1
                end_col = len(lines[-1])
            
            buffer.selection.set_start(self.line, self.col)
            buffer.selection.set_end(end_line, end_col)
        
    def redo(self, buffer: UndoableBuffer) -> None:
        """Redo the deletion"""
        # Calculate range
        lines = self.text.split('\n')
        if len(lines) == 1:
            end_line = self.line
            end_col = self.col + len(self.text)
        else:
            end_line = self.line + len(lines) - 1
            end_col = len(lines[-1])
            
        buffer.selection.set_start(self.line, self.col)
        buffer.selection.set_end(end_line, end_col)
        buffer.delete_selection(_record_undo=False)

    def merge(self, other: UndoCommand) -> bool:
        """Merge consecutive deletion operations"""
        if not isinstance(other, DeleteCommand):
            return False
            
        # Merge sequential backward deletes (Backspace)
        # Sequence: del 'c' at (0,2), then del 'b' at (0,1)
        # other is new command.
        
        lines_other = other.text.split('\n')
        if len(lines_other) == 1:
            # Backward delete merge
            if other.line == self.line and (other.col + len(other.text)) == self.col:
                self.col = other.col
                self.text = other.text + self.text
                self.timestamp = other.timestamp
                return True
             
            # Forward delete merge (Delete key)
            if other.line == self.line and other.col == self.col:
                self.text += other.text
                self.timestamp = other.timestamp
                return True
                 
        return False


class CompositeCommand(UndoCommand):
    """Represents a group of commands executed as a single undo step"""
    
    def __init__(self, commands: List[UndoCommand]):
        self.commands = commands  # List of commands in execution order
        
    def undo(self, buffer: UndoableBuffer) -> None:
        """Undo all commands in reverse order"""
        for cmd in reversed(self.commands):
            cmd.undo(buffer)
            
    def redo(self, buffer: UndoableBuffer) -> None:
        """Redo all commands in execution order"""
        for cmd in self.commands:
            cmd.redo(buffer)
            
    def merge(self, other: UndoCommand) -> bool:
        """Composite commands don't merge"""
        return False


# ============================================================
#   UNDO STACK
# ============================================================

class UndoStack:
    """Manages undo/redo stacks with command merging support"""
    
    def __init__(self, buffer: UndoableBuffer, max_size: int = 1000):
        self.buffer = buffer
        self.undo_stack: List[UndoCommand] = []
        self.redo_stack: List[UndoCommand] = []
        self.max_size = max_size
        self.is_doing_undo = False
        
    def add_command(self, cmd: UndoCommand) -> None:
        """Add a command to the undo stack, attempting to merge if possible"""
        if self.is_doing_undo:
            return
            
        # Try to merge with top of stack
        if self.undo_stack:
            if self.undo_stack[-1].merge(cmd):
                return
                
        self.undo_stack.append(cmd)
        self.redo_stack.clear()
        
        if len(self.undo_stack) > self.max_size:
            self.undo_stack.pop(0)
            
    def undo(self) -> None:
        """Undo the last command"""
        if not self.undo_stack:
            return
            
        cmd = self.undo_stack.pop()
        self.redo_stack.append(cmd)
        
        self.is_doing_undo = True
        try:
            cmd.undo(self.buffer)
        finally:
            self.is_doing_undo = False
            
    def redo(self) -> None:
        """Redo the last undone command"""
        if not self.redo_stack:
            return
            
        cmd = self.redo_stack.pop()
        self.undo_stack.append(cmd)
        
        self.is_doing_undo = True
        try:
            cmd.redo(self.buffer)
        finally:
            self.is_doing_undo = False

    def can_undo(self) -> bool:
        """Check if undo is available"""
        return len(self.undo_stack) > 0
        
    def can_redo(self) -> bool:
        """Check if redo is available"""
        return len(self.redo_stack) > 0

    def clear(self) -> None:
        """Clear both undo and redo stacks"""
        self.undo_stack.clear()
        self.redo_stack.clear()


# ============================================================
#   INSTALLATION / INTEGRATION
# ============================================================

def has_undo_redo_support() -> bool:
    """Check if all dependencies are available for undo/redo feature"""
    # This module has no external dependencies beyond Python stdlib
    return True


def install_undo_redo(buffer_class: type) -> bool:
    """
    Install undo/redo functionality into a buffer class.
    
    This function doesn't modify the class directly, but validates that
    the buffer class has the required interface. The actual integration
    happens when the buffer creates an UndoStack instance.
    
    Args:
        buffer_class: The buffer class to validate
        
    Returns:
        True if the buffer class has the required interface, False otherwise
    """
    required_methods = ['insert_text', 'delete_selection', 'begin_action', 'end_action']
    required_attrs = ['cursor_line', 'cursor_col', 'selection']
    
    # Check for required methods
    for method in required_methods:
        if not hasattr(buffer_class, method):
            print(f"Warning: Buffer class missing required method: {method}")
            return False
    
    # Note: We can't easily check for attributes on the class itself,
    # they're typically instance attributes. This is just a basic check.
    
    return True


# Export public API
__all__ = [
    'UndoCommand',
    'InsertCommand',
    'DeleteCommand',
    'CompositeCommand',
    'UndoStack',
    'install_undo_redo',
    'has_undo_redo_support',
    'UndoableBuffer',
    'SelectionProtocol'
]
