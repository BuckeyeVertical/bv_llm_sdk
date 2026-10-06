"""Move east, north, and up relative to the starting position.

Usage:
    uv run scripts/goto.py 10 20 5
"""

import sys

from bv_llm_sdk import goto

DEFAULT_UP_M = 20.0
DEFAULT_EAST_M = 0.0
DEFAULT_NORTH_M = 0.0

if __name__ == "__main__":  
    east_m = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EAST_M
    north_m = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_NORTH_M
    up_m = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_UP_M
    goto(up_m, east_m, north_m)
    print(f"moved east {east_m}, north {north_m}, up {up_m} m")
