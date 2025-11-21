import sys

def log(msg):
    print(msg, flush=True)
    print(msg, file=sys.stderr, flush=True)

class Selection:
    def __init__(self):
        self.active = False
        self.start_line = 0
        self.start_col = 0
        self.end_line = 0
        self.end_col = 0

    def clear(self):
        self.active = False

    def set_start(self, ln, col):
        self.start_line = ln
        self.start_col = col
        self.end_line = ln
        self.end_col = col
        self.active = True

    def set_end(self, ln, col):
        self.end_line = ln
        self.end_col = col
        self.active = True

    def has_selection(self):
        return self.active and (
            self.start_line != self.end_line or 
            self.start_col != self.end_col
        )

    def get_bounds(self):
        if not self.active:
            return None
        s_ln, s_col = self.start_line, self.start_col
        e_ln, e_col = self.end_line, self.end_col
        if s_ln > e_ln or (s_ln == e_ln and s_col > e_col):
            return e_ln, e_col, s_ln, s_col
        return s_ln, s_col, e_ln, e_col

class VirtualBuffer:
    def __init__(self):
        self.edits = {}
        self.inserted_lines = {}
        self.deleted_lines = set()
        self.cursor_line = 0
        self.cursor_col = 0
        self.selection = Selection()

    def get_line(self, ln):
        if ln in self.inserted_lines:
            return self.inserted_lines[ln]
        if ln in self.edits:
            return self.edits[ln]
        return ""

    def emit(self, *args): pass
    def _add_offset(self, *args): pass

    def get_selected_text(self):
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
        if not self.selection.has_selection():
            return False
        
        start_line, start_col, end_line, end_col = self.selection.get_bounds()
        
        if start_line == end_line:
            line = self.get_line(start_line)
            new_line = line[:start_col] + line[end_col:]
            if start_line in self.inserted_lines:
                self.inserted_lines[start_line] = new_line
            else:
                self.edits[start_line] = new_line
        else:
            first_line = self.get_line(start_line)
            last_line = self.get_line(end_line)
            new_line = first_line[:start_col] + last_line[end_col:]
            
            lines_deleted = end_line - start_line
            
            new_ins = {}
            for k, v in self.inserted_lines.items():
                if k < start_line:
                    new_ins[k] = v
                elif k == start_line:
                    pass
                elif k <= end_line:
                    pass
                else:
                    new_ins[k - lines_deleted] = v
            
            new_ed = {}
            for k, v in self.edits.items():
                if k < start_line:
                    new_ed[k] = v
                elif k == start_line:
                    pass
                elif k <= end_line:
                    pass
                else:
                    new_ed[k - lines_deleted] = v
            
            if start_line in self.inserted_lines:
                new_ins[start_line] = new_line
            else:
                new_ed[start_line] = new_line
            
            self.inserted_lines = new_ins
            self.edits = new_ed
            
        self.cursor_line = start_line
        self.cursor_col = start_col
        self.selection.clear()
        return True

    def insert_text(self, text):
        if self.selection.has_selection():
            self.delete_selection()

        ln  = self.cursor_line
        col = self.cursor_col
        old = self.get_line(ln)

        parts = text.split("\n")

        if len(parts) == 1:
            new_line = old[:col] + text + old[col:]
            if ln in self.inserted_lines:
                self.inserted_lines[ln] = new_line
            else:
                self.edits[ln] = new_line
            self.cursor_col += len(text)
        else:
            left_part = old[:col] + parts[0]
            right_part = parts[-1] + old[col:]
            middle = parts[1:-1]
            
            lines_to_insert = len(parts) - 1
            
            new_ins = {}
            for k, v in self.inserted_lines.items():
                new_ins[k if k <= ln else k+lines_to_insert] = v
            
            new_ed = {}
            for k, v in self.edits.items():
                new_ed[k if k <= ln else k+lines_to_insert] = v
            
            if ln in self.inserted_lines:
                new_ins[ln] = left_part
            else:
                new_ed[ln] = left_part
            
            cur = ln
            for m in middle:
                cur += 1
                new_ins[cur] = m
            
            new_ins[ln + lines_to_insert] = right_part
            
            self.inserted_lines = new_ins
            self.edits = new_ed
            
            self.cursor_line = ln + lines_to_insert
            self.cursor_col  = len(parts[-1])

        self.selection.clear()

    def move_word_left_with_text(self):
        ln = self.cursor_line
        col = self.cursor_col
        line = self.get_line(ln)
        
        # BUG: This check prevents moving if cursor is on empty line
        if not line:
            log("ABORT: Line is empty")
            return
        
        if self.selection.has_selection():
            bounds = self.selection.get_bounds()
            if bounds and bounds[0] is not None:
                start_ln, start_col, end_ln, end_col = bounds
                
                if start_ln != end_ln:
                    if start_col > 0:
                        prev_ln = start_ln
                        prev_col = start_col - 1
                        char_before = self.get_line(start_ln)[start_col - 1]
                    else:
                        if start_ln == 0: return
                        prev_ln = start_ln - 1
                        prev_line_content = self.get_line(prev_ln)
                        prev_col = len(prev_line_content)
                        char_before = '\n'
                        
                    selected_text = self.get_selected_text()
                    
                    self.selection.set_start(prev_ln, prev_col)
                    self.selection.set_end(end_ln, end_col)
                    
                    self.delete_selection()
                    
                    full_text = selected_text + char_before
                    
                    ins_start_ln = self.cursor_line
                    ins_start_col = self.cursor_col
                    
                    self.insert_text(full_text)
                    
                    sel_lines = selected_text.split('\n')
                    if len(sel_lines) == 1:
                        sel_end_ln = ins_start_ln
                        sel_end_col = ins_start_col + len(selected_text)
                    else:
                        sel_end_ln = ins_start_ln + len(sel_lines) - 1
                        sel_end_col = len(sel_lines[-1])
                    
                    self.selection.set_start(ins_start_ln, ins_start_col)
                    self.selection.set_end(sel_end_ln, sel_end_col)
                    return

def test():
    log("Starting test")
    buf = VirtualBuffer()
    # Setup: L0="A", L1=""
    buf.edits[0] = "A"
    buf.edits[1] = ""
    
    # Cursor on L1 (empty)
    buf.cursor_line = 1
    buf.cursor_col = 0
    
    # Selection: "A" on L0? No, let's try to move UP from empty line.
    # Select L0 "A" to L1 start? No.
    # Let's reproduce the stuck state:
    # L2="twoe", L3="t", L4=""
    # Sel: "o\nt" (2, 3) - (3, 1).
    # Cursor at L4 (4, 0).
    
    buf.edits[2] = "twoe"
    buf.edits[3] = "t"
    buf.edits[4] = ""
    
    buf.selection.set_start(2, 3)
    buf.selection.set_end(3, 1)
    buf.cursor_line = 4
    buf.cursor_col = 0
    
    log(f"Initial: 4='{buf.get_line(4)}'")
    
    buf.move_word_left_with_text()
    
    # Check if moved
    log(f"After: 2='{buf.get_line(2)}', 3='{buf.get_line(3)}', 4='{buf.get_line(4)}'")

if __name__ == "__main__":
    test()
