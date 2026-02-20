import sys
sys.path.insert(0, r"c:\Users\38370\Desktop\春晚弹幕")

from douyu_service import _fetch_room_info

room_url = "https://www.douyu.com/178432"
info = _fetch_room_info(room_url)

print("\n=== Room Info ===")
print(f"Host: {info['host']}")
print(f"Title: {info['title']}")
print(f"Live Time: {info['live_time']}")
