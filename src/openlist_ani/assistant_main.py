"""
Thin entry-point shim kept for backward compatibility.

All logic lives in ``openlist_ani.assistant``.
"""

from .assistant import main

if __name__ == "__main__":
    main()
