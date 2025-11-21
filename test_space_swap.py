#!/usr/bin/env python3
"""Test space preservation in multi-line character swaps."""

# Test case from user:
# Line 1: "one " (with trailing space)
# Line 2: "two"
# Selection: "one \ntwo" (full selection across both lines)
# Expected after Alt+Right: "two \none" (space stays with "one")
# Current behavior: " two\none" (space moves incorrectly)

print("Test: Space preservation in multi-line swap")
print("=" * 50)
print()
print("Initial state:")
print("Line 1: 'one '")
print("Line 2: 'two'")
print("Selection: 'one \\ntwo'")
print()
print("Expected after swap:")
print("Line 1: 'two '")
print("Line 2: 'one'")
print()
print("The issue: Currently the space moves with the newline")
print("instead of staying with 'one'")
