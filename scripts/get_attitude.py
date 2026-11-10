""" Returns the vehicle's yaw, pitch, and roll in degrees

usage: uv run scripts/get_attiude.py

"""
from bv_llm_sdk import get_attitude

if __name__ == "__main__":
    attitude = get_attitude()
    print(f"yaw: {attitude["yaw"]:4f}°"
          f"\n pitch: {attitude["pitch"]:4f}°"
          f"\n roll: {attitude["roll"]:4f}°"
          )