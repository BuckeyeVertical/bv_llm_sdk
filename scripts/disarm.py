"""
Disarms the motors

Usage:
    uv run scripts/disarm.py
"""
from bv_llm_sdk import disarm

if __name__ == "__main__":
    disarm()
    print("Disarmed")

