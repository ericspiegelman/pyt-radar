#!/usr/bin/env python3
"""
build_grid.py — turn YouTube links/playlists into a weekly-grid CSV for the
FAST Channel template (day, start, video, title, duration).

Resolves each link with yt-dlp (no API key needed; playlists auto-expand),
then packs the videos across the week so the schedule is gapless 24/7.

USAGE
  # one pool repeated across all 7 days, filling 24h/day:
  python3 build_grid.py links.txt > grid.csv

  # per-day pools — group links under "# Mon", "# Tue", ... headers in the file:
  python3 build_grid.py links.txt --by-day > grid.csv

  # quick self-test with built-in demo videos (no network/yt-dlp):
  python3 build_grid.py --demo > grid.csv

links.txt: one YouTube URL (or playlist URL, or bare ID) per line. Blank lines
and lines starting with "#" are ignored (except "# Mon" day headers in --by-day).
"""
import sys, json, subprocess, argparse

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_SECONDS = 24 * 3600

DEMO = [  # (id, title, duration_seconds) — real videos, real lengths
    ("aqz-KE-bpKQ", "Big Buck Bunny (Demo)", 634),
    ("dQw4w9WgXcQ", "Demo Program — Block B", 213),
    ("YE7VzlLtp-4", "Big Buck Bunny — Trailer", 33),
    ("jNQXAC9IVRw", "Me at the zoo (Demo)", 19),
]


def hhmmss(sec):
    sec = int(round(sec))
    return f"{sec//3600:02d}:{sec%3600//60:02d}:{sec%60:02d}"


def resolve(urls):
    """Return [(id, title, duration_seconds)] for the given URLs via yt-dlp."""
    out = []
    for url in urls:
        try:
            raw = subprocess.run(
                ["yt-dlp", "-J", "--flat-playlist", "--no-warnings", url],
                capture_output=True, text=True, timeout=120, check=True,
            ).stdout
            data = json.loads(raw)
        except Exception as e:
            print(f"  ! skipped {url}: {e}", file=sys.stderr)
            continue
        entries = data.get("entries") if data.get("_type") == "playlist" else [data]
        for e in entries or []:
            if not e:
                continue
            vid, dur = e.get("id"), e.get("duration")
            # --flat-playlist omits duration; fetch it per-video when missing.
            if not dur and vid:
                try:
                    j = json.loads(subprocess.run(
                        ["yt-dlp", "-J", "--no-warnings", f"https://youtu.be/{vid}"],
                        capture_output=True, text=True, timeout=120, check=True).stdout)
                    dur, title = j.get("duration"), j.get("title")
                except Exception as ex:
                    print(f"  ! no duration for {vid}: {ex}", file=sys.stderr)
                    continue
            else:
                title = e.get("title")
            if vid and dur:
                out.append((vid, (title or vid).strip(), int(dur)))
    return out


def pack_day(pool, day):
    """Pack `pool` (repeating) from 00:00 until the 24h day is full."""
    rows, t, i = [], 0, 0
    if not pool:
        return rows
    while t < DAY_SECONDS:
        vid, title, dur = pool[i % len(pool)]
        rows.append((day, hhmmss(t), f"https://youtu.be/{vid}", title, hhmmss(dur)))
        t += dur
        i += 1
    return rows


def read_links(path, by_day):
    """Read links.txt -> {day: [urls]}; without --by-day everything is one pool."""
    groups, cur = {d: [] for d in DAYS}, None
    pool = []
    for line in open(path, encoding="utf-8"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            tag = s.lstrip("#").strip()[:3].title()
            if by_day and tag in DAYS:
                cur = tag
            continue
        (groups[cur] if (by_day and cur) else pool).append(s)
    return groups if by_day else pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("links", nargs="?", help="path to links.txt")
    ap.add_argument("--by-day", action="store_true", help="group links under # Mon/# Tue headers")
    ap.add_argument("--demo", action="store_true", help="use built-in demo videos")
    args = ap.parse_args()

    rows = []
    if args.demo:
        for d in DAYS:
            rows += pack_day(DEMO, d)
    elif args.by_day:
        src = read_links(args.links, True)
        for d in DAYS:
            print(f"Resolving {len(src[d])} link(s) for {d}…", file=sys.stderr)
            rows += pack_day(resolve(src[d]), d)
    elif args.links:
        pool = resolve(read_links(args.links, False))
        print(f"Resolved {len(pool)} videos; packing across all 7 days…", file=sys.stderr)
        for d in DAYS:
            rows += pack_day(pool, d)
    else:
        ap.error("give a links file, or use --demo")

    print("day,start,video,title,duration")
    for day, start, video, title, dur in rows:
        t = '"' + title.replace('"', '""') + '"'
        print(f"{day},{start},{video},{t},{dur}")


if __name__ == "__main__":
    main()
