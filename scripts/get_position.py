""" Returns the vehicle's latitude, longitude, and heading in degrees

usage: uv run scripts/get_position.py

"""
from bv_llm_sdk import get_position

if __name__ == "__main__":
    position = get_position()
    print(f"latitude: {position["latitude"]}°"
          f"\nlongitude: {position["longitude"]}°"
          f"\nheading: {position["heading_deg"]}°"
          )


