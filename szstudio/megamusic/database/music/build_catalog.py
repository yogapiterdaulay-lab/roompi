import json
import os
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

REGIONS = ["kr", "tw", "id", "gb", "us", "jp"]
YT_API_KEY = os.environ.get("YT_API_KEY", "")
CATALOG_PATH = Path("data/catalog.json")
REGIONS_PATH = Path("data/regions.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MegaMusicPlayer-Builder/1.0)",
    "Accept": "application/json",
}

def fetch_json(url, retries=3, timeout=30):
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as res:
                return json.loads(res.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(2 * attempt)
    raise last_err

def fetch_region_chart(region):
    # Primary: Apple Marketing Tools RSS
    primary_url = f"https://rss.marketingtools.apple.com/api/v2/{region}/music/most-played/100/songs.json"
    try:
        data = fetch_json(primary_url, retries=2, timeout=25)
        results = data["feed"]["results"]
        tracks = []
        for track in results:
            art = re.sub(r"\d+x\d+bb", "200x200bb", track["artworkUrl100"])
            tracks.append({
                "apple_id": str(track["id"]),
                "title": track["name"],
                "artist": track["artistName"],
                "artwork_url": art,
            })
        return tracks
    except Exception as primary_err:
        print(f"[{region}] Primary RSS timed out ({primary_err}), switching to fallback feed...")

    # Fallback: Akamai-cached iTunes RSS
    fallback_url = f"https://itunes.apple.com/{region}/rss/topsongs/limit=100/json"
    data = fetch_json(fallback_url, retries=3, timeout=30)
    entries = data.get("feed", {}).get("entry", [])
    tracks = []
    for entry in entries:
        raw_art = entry["im:image"][-1]["label"]
        art = re.sub(r"\d+x\d+bb", "200x200bb", raw_art)
        tracks.append({
            "apple_id": str(entry["id"]["attributes"]["im:id"]),
            "title": entry["im:name"]["label"],
            "artist": entry["im:artist"]["label"],
            "artwork_url": art,
        })
    return tracks

def resolve_youtube_id(artist, title):
    if not YT_API_KEY:
        return ""
    q = urllib.parse.quote(f"{artist} - {title} official")
    url = (
        f"https://www.googleapis.com/youtube/v3/search"
        f"?part=snippet&type=video&videoEmbeddable=true&maxResults=1&q={q}&key={YT_API_KEY}"
    )
    try:
        data = fetch_json(url, retries=2, timeout=20)
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
    if REGIONS_PATH.exists():
        regions_out = json.loads(REGIONS_PATH.read_text(encoding="utf-8"))

    pending_yt = []

    # 1. Fetch all 6 regions with retry + fallback
    for region in REGIONS:
        try:
            feed = fetch_region_chart(region)
            print(f"[{region}] Fetched {len(feed)} tracks.")
        except Exception as e:
            print(f"[{region}] Skipped due to network error: {e}")
            continue

        region_tracks = []
        for rank, track in enumerate(feed, start=1):
            apple_id = track["apple_id"]
            entry = catalog.get(apple_id, {
                "apple_id": apple_id,
                "title": track["title"],
                "artist": track["artist"],
                "artwork_url": track["artwork_url"],
                "regions": [],
                "yt_id": "",
            })

            if region not in entry["regions"]:
                entry["regions"].append(region)

            catalog[apple_id] = entry
            if not entry["yt_id"]:
                pending_yt.append((rank, apple_id))

            region_tracks.append({
                "rank": rank,
                "apple_id": apple_id,
            })

        regions_out[region] = region_tracks
        time.sleep(0.5)

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
