#!/usr/bin/env python3
import sys

def detect_encoding(path):
    with open(path, "rb") as f:
        data = f.read(4096)  # small peek is enough

    # --- BOM detection ---
    if data.startswith(b"\xff\xfe"):
        return "utf-16le"
    if data.startswith(b"\xfe\xff"):
        return "utf-16be"
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    # --- Heuristic UTF-16LE detection (no BOM) ---
    # Check for many zero bytes in odd positions
    if len(data) >= 4:
        zeros_in_odd = sum(1 for i in range(1, len(data), 2) if data[i] == 0)
        ratio = zeros_in_odd / (len(data) / 2)
        if ratio > 0.4:  # UTF-16LE usually >50%, 40% is safe threshold
            return "utf-16le"

    # --- Heuristic UTF-16BE detection (no BOM) ---
    zeros_in_even = sum(1 for i in range(0, len(data), 2) if data[i] == 0)
    ratio_be = zeros_in_even / (len(data) / 2)
    if ratio_be > 0.4:
        return "utf-16be"

    # Default
    return "utf-8"


def main():
    if len(sys.argv) != 2:
        print("Usage: detect.py <filename>")
        sys.exit(1)

    try:
        enc = detect_encoding(sys.argv[1])
        print(enc)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
