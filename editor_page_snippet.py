class EditorPage(Gtk.Grid):
    def __init__(self):
        super().__init__()
        self.set_column_spacing(0)
        self.set_row_spacing(0)
        self.set_css_classes(["editor-surface"])

        self.current_encoding = "utf-8"
        self.path = None

        self.buf = VirtualBuffer()
        self.view = VirtualTextView(self.buf)
        self.vscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL, adjustment=self.view.vadj)
        self.hscroll = Gtk.Scrollbar(orientation=Gtk.Orientation.HORIZONTAL, adjustment=self.view.hadj)

        self.vscroll.add_css_class("overlay-scrollbar")
        self.hscroll.add_css_class("hscrollbar-overlay")
        self.vscroll.set_visible(False)
        self.hscroll.set_visible(False)

        self.view.vscroll = self.vscroll
        self.view.hscroll = self.hscroll

        # Attach to grid
        self.attach(self.view, 0, 0, 1, 1)
        self.vscroll.set_hexpand(False)
        self.vscroll.set_vexpand(True)
        self.attach(self.vscroll, 1, 0, 1, 1)
        self.hscroll.set_hexpand(True)
        self.hscroll.set_vexpand(False)
        self.attach(self.hscroll, 0, 1, 1, 1)
        
        corner = Gtk.Box()
        corner.set_size_request(12, 12)
        self.attach(corner, 1, 1, 1, 1)

    def get_title(self):
        if self.path:
            return os.path.basename(self.path)
        return "Untitled"
