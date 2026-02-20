import urllib.request
import re
import json

room_url = "https://www.douyu.com/178432"

print(f"Fetching: {room_url}")
try:
    with urllib.request.urlopen(room_url, timeout=10) as response:
        html = response.read().decode("utf-8", errors="ignore")
    
    print(f"HTML length: {len(html)} chars\n")
    
    print("=== Method 1: H1 tag (room title) ===")
    h1_match = re.search(r'<h1[^>]*class="roomName[^"]*"[^>]*>([^<]+)</h1>', html)
    if h1_match:
        print(f'Room Title: "{h1_match.group(1)}"')
    
    print("\n=== Method 2: IMG title (anchor name) ===")
    img_match = re.search(r'<img[^>]+title="([^"]+)"[^>]*(?:alt="[^"]*土耳其|alt="[^"]*PGL)', html)
    if img_match:
        print(f'Anchor Name: "{img_match.group(1)}"')
    
    # Alternative: find by class
    img_match2 = re.search(r'title="([^"]+)"[^>]*alt="土耳其[^"]*"', html)
    if img_match2:
        print(f'Anchor Name (alt2): "{img_match2.group(1)}"')
    
    print("\n=== Method 3: Meta tags ===")
    og_title = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if og_title:
        print(f'OG Title: "{og_title.group(1)}"')
    
    print("\n=== Method 4: Looking for JSON data ===")
    # Try to find embedded JSON with room data
    json_match = re.search(r'"roomName"\s*:\s*"([^"]+)"', html)
    if json_match:
        print(f'roomName (JSON): "{json_match.group(1)}"')
    
    owner_match = re.search(r'"owner_name"\s*:\s*"([^"]+)"', html)
    if owner_match:
        print(f'owner_name (JSON): "{owner_match.group(1)}"')
    
    nickname_match = re.search(r'"nickname"\s*:\s*"([^"]+)"', html)
    if nickname_match:
        print(f'nickname (JSON): "{nickname_match.group(1)}"')
    
    # Look for show_time
    show_time_match = re.search(r'"show_time"\s*:\s*"([^"]+)"', html)
    if show_time_match:
        print(f'show_time (JSON): "{show_time_match.group(1)}"')
    
    start_time_match = re.search(r'"start_time"\s*:\s*"([^"]+)"', html)
    if start_time_match:
        print(f'start_time (JSON): "{start_time_match.group(1)}"')
    
    print("\n=== Recommended extraction ===")
    title = ""
    host = ""
    
    # Best method for title: h1 tag
    h1_match = re.search(r'<h1[^>]*>([^<]+)</h1>', html)
    if h1_match:
        title = h1_match.group(1).strip()
    
    # Best method for host: extract from og:title or img title
    og_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if og_match:
        # Format: "房间标题_主播昵称直播_主播全名直播_..."
        og_text = og_match.group(1)
        parts = og_text.split('_')
        if len(parts) >= 3:
            # Third part usually contains full name
            host = parts[2].replace('直播', '').strip()
    
    print(f'Title: "{title}"')
    print(f'Host: "{host}"')
    
except Exception as exc:
    print(f"Error: {exc}")
