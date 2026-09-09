"""Take off and climb to an altitude above the launch point.

Usage:
    uv run scripts/takeoff.py          # 20 m
    uv run scripts/takeoff.py 35       # 35 m
"""

import sys

from bv_llm_sdk import takeoff

DEFAULT_ALTITUDE_M = 20.0

if __name__ == "__main__":
    altitude_m = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ALTITUDE_M
    takeoff(altitude_m)
    print(f"at {altitude_m} m")
