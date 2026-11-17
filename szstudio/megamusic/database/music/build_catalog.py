import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from ytmusicapi import YTMusic
    YTM = YTMusic()
except Exception as e:
    print(f"[WARN] ytmusicapi unavailable: {e}")
    YTM = None

REGIONS = ["kr", "tw", "id", "gb", "us", "jp"]
YT_API_KEY = os.environ.get("YT_API_KEY", "").strip()
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
        print(f"[{region}] Primary RSS timed out ({primary_err}), using fallback...")

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

def resolve_via_ytmusic(artist, title):
    if YTM is None:
        return ""
    try:
        results = YTM.search(f"{artist} {title}", filter="songs", limit=1)
        if results and results[0].get("videoId"):
            return results[0]["videoId"]
        # Broader fallback if exact "songs" filter returns empty
        results = YTM.search(f"{artist} {title}", limit=1)
        if results and results[0].get("videoId"):
            return results[0]["videoId"]
    except Exception as e:
        print(f"[YTMusic error] {artist} - {title}: {e}")
    return ""

def resolve_via_yt_api(artist, title):
    if not YT_API_KEY:
        return ""
    q = urllib.parse.quote(f'{artist} - {title} "official audio"')
    url = (
        f"https://www.googleapis.com/youtube/v3/search"
        f"?part=snippet&type=video&videoCategoryId=10&videoEmbeddable=true&maxResults=1&q={q}&key={YT_API_KEY}"
    )
    try:
        data = fetch_json(url, retries=1, timeout=20)
        items = data.get("items", [])
        return items[0]["id"]["videoId"] if items else ""
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        print(f"[YT API HTTP {e.code}] {body[:200]}")
        return ""
    except Exception as e:
        print(f"[YT API error] {e}")
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

    # 1. Refresh charts across all 6 regions
    for region in REGIONS:
        try:
            feed = fetch_region_chart(region)
            print(f"[{region}] Fetched {len(feed)} tracks.")
        except Exception as e:
            print(f"[{region}] Skipped due to error: {e}")
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
            if not entry.get("yt_id"):
                pending_yt.append((rank, apple_id))

            region_tracks.append({
                "rank": rank,
                "apple_id": apple_id,
            })

        regions_out[region] = region_tracks
        time.sleep(0.3)

    # Also queue any existing catalog tracks still missing yt_id
    queued_ids = {aid for _, aid in pending_yt}
    for apple_id, entry in catalog.items():
        if not entry.get("yt_id") and apple_id not in queued_ids:
            pending_yt.append((999, apple_id))

    # 2. Resolve missing yt_ids (up to 300 per run)
    seen = set()
    resolved_count = 0
    yt_api_calls = 0

    for _, apple_id in sorted(pending_yt, key=lambda x: x[0]):
        if apple_id in seen or resolved_count >= 300:
            continue
        seen.add(apple_id)
        entry = catalog[apple_id]

        vid = resolve_via_ytmusic(entry["artist"], entry["title"])
        if not vid and YT_API_KEY and yt_api_calls < 80:
            vid = resolve_via_yt_api(entry["artist"], entry["title"])
            yt_api_calls += 1

        if vid:
            entry["yt_id"] = vid
            resolved_count += 1

        time.sleep(0.25)

    total_with_yt = sum(1 for v in catalog.values() if v.get("yt_id"))
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_PATH.write_text(json.dumps(list(catalog.values()), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    REGIONS_PATH.write_text(json.dumps(regions_out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Done. Total catalog: {len(catalog)} | Resolved this run: {resolved_count} | Total with yt_id: {total_with_yt}/{len(catalog)}")

if __name__ == "__main__":
    main()
