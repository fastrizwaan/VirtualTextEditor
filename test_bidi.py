import unicodedata

class BiDiUtils:
    @staticmethod
    def is_rtl_line(text):
        if not text: return False
        for ch in text:
            b = unicodedata.bidirectional(ch)
            if b in ("R", "AL", "RLE", "RLO"):
                return True
            if b in ("L", "LRE", "LRO"):
                return False
        return False

# Test cases
texts = [
    ("Arabic", "مرحبا"),
    ("Arabic with spaces", "   مرحبا"),
    ("Arabic with numbers", "123 مرحبا"),
    ("Mixed starts with Arabic", "مرحبا abc"),
    ("Mixed starts with English", "abc مرحبا"),
    ("Numbers only", "123"),
    ("Punctuation only", "..."),
    ("User Screenshot Approx", "فلكج سادفكج اسدفل") 
]

for name, text in texts:
    is_rtl = BiDiUtils.is_rtl_line(text)
    print(f"'{name}': {is_rtl}")
