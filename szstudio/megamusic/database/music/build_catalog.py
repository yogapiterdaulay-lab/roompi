import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

REGIONS = ["kr", "tw", "id", "gb", "us", "jp"]
YT_API_KEY = os.environ.get("YT_API_KEY", "")
CATALOG_PATH = Path("data/catalog.json")
REGIONS_PATH = Path("data/regions.json")

def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "MegaMusicPlayer-Builder/1.0"})
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode("utf-8"))

def resolve_youtube_id(artist, title):
    if not YT_API_KEY:
        return ""
    q = urllib.parse.quote(f"{artist} - {title} official")
    url = (
        f"https://www.googleapis.com/youtube/v3/search"
        f"?part=snippet&type=video&videoEmbeddable=true&maxResults=1&q={q}&key={YT_API_KEY}"
    )
    try:
        data = fetch_json(url)
        items = data.get("items", [])
        return items[0]["id"]["videoId"] if items else ""
    except Exception:
        return ""

def main():
    catalog = {}
    if CATALOG_PATH.exists():
        for item in json.loads(CATALOG_PATH.read_text(encoding="utf-8")):
            catalog[item["apple_id"]] = item

    regions_out = {}
    pending_yt = []  # (best_rank, apple_id)

    # 1. Fetch all 6 regions first (0 YouTube quota)
    for region in REGIONS:
        rss_url = f"https://rss.marketingtools.apple.com/api/v2/{region}/music/most-played/100/songs.json"
        feed = fetch_json(rss_url)["feed"]["results"]
        region_tracks = []

        for rank, track in enumerate(feed, start=1):
            apple_id = track["id"]
            title = track["name"]
            artist = track["artistName"]
            art_200 = track["artworkUrl100"].replace("100x100bb", "200x200bb")

            entry = catalog.get(apple_id, {
                "apple_id": apple_id,
                "title": title,
                "artist": artist,
                "artwork_url": art_200,
                "regions": [],
                "yt_id": ""
            })

            if region not in entry["regions"]:
                entry["regions"].append(region)

            catalog[apple_id] = entry
            if not entry["yt_id"]:
                pending_yt.append((rank, apple_id))

            region_tracks.append({
                "rank": rank,
                "apple_id": apple_id
            })

        regions_out[region] = region_tracks

    # 2. Resolve highest-ranked missing tracks first (cap at 85/day)
    seen = set()
    yt_calls_used = 0
    for _, apple_id in sorted(pending_yt, key=lambda x: x[0]):
        if apple_id in seen or yt_calls_used >= 85:
            continue
        seen.add(apple_id)
        entry = catalog[apple_id]
        entry["yt_id"] = resolve_youtube_id(entry["artist"], entry["title"])
        yt_calls_used += 1
        time.sleep(0.2)

    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(list(catalog.values()), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    REGIONS_PATH.write_text(json.dumps(regions_out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Done. Catalog: {len(catalog)} tracks | YT resolved this run: {yt_calls_used}")

if __name__ == "__main__":
    main()
