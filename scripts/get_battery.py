""""Print the vehicle's remaining battery charge.

Usage:
    uv run scripts/get_battery.py
"""

from bv_llm_sdk import get_battery

if __name__ == "__main__":
    print(f"battery: {get_battery()}%")
