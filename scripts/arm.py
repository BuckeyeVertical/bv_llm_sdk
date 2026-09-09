"""Arm the motors.

Usage:
    uv run scripts/arm.py
"""

from bv_llm_sdk import arm

if __name__ == "__main__":
    arm()
    print("armed")
