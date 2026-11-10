""" Returns the vehicle's latitude, longitude, and heading in degrees

usage: uv run scripts/get_position.py

"""
from bv_llm_sdk import get_position

if __name__ == "__main__":
    position = get_position()
    print(f"latitude: {position["latitude"]:4f}°"
          f"\nlongitude: {position["longitude"]:4f}°"
          f"\nheading: {position["heading_deg"]:4f}°"
          )


