"""Return vehicle to launch position

Usage:
    uv run scripts/rtl.py

"""

from bv_llm_sdk import rtl

if __name__ == "__main__":
    rtl()
    print("Returning to launch position")
    