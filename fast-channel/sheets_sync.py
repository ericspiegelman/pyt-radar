#!/usr/bin/env python3
"""
sheets_sync.py — read/write the FAST Channel schedule directly in Google Sheets.

Uses a Google **service account** so writes are fully headless (no browser
consent). Reuses build_grid.py to resolve YouTube links (titles + durations via
yt-dlp) and pack them into the weekly grid, then writes the grid into the sheet
IN PLACE (clears the tab and rewrites it) — preserving the sheet's URL/sharing.

ONE-TIME SETUP (only you can do these — they touch your Google account):
  1. Google Cloud Console → your project → APIs & Services → enable
     "Google Sheets API".
  2. IAM & Admin → Service Accounts → Create service account → Done.
  3. On that service account → Keys → Add key → JSON → download it.
  4. Save the JSON as:  fast-channel/service_account.json   (git-ignored)
  5. Open your Google Sheet → Share → add the service account's email
     (looks like ...@...iam.gserviceaccount.com) as **Editor**.

CONFIG (env vars, with sensible defaults):
  GOOGLE_SHEETS_CREDS   path to the service-account JSON
                        (default: fast-channel/service_account.json)
  FAST_SHEET_ID         the target spreadsheet ID

USAGE:
  python sheets_sync.py --read                         # dump current grid
  python sheets_sync.py --links links.txt              # one pool, all 7 days
  python sheets_sync.py --links links.txt --by-day     # per-day (# Mon headers)
  python sheets_sync.py --csv grid.csv                 # write a prebuilt CSV
  python sheets_sync.py --whoami                        # print SA email to share with
"""
import os, sys, csv, argparse, io

import gspread
from google.oauth2.service_account import Credentials

import build_grid  # resolve(), read_links(), pack_day(), hhmmss(), DAYS

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDS_PATH = os.environ.get("GOOGLE_SHEETS_CREDS",
                            os.path.join(os.path.dirname(__file__), "service_account.json"))
# Default target = the "Griffith FAST Channel — Schedule" sheet created earlier.
SHEET_ID = os.environ.get("FAST_SHEET_ID", "1wh-Uhm7uNp0N4Lbc-aZmX4XIKXzfpBEwDX51C2f1v74")

HEADER = ["day", "start", "video", "title", "duration"]


def creds():
    if not os.path.exists(CREDS_PATH):
        sys.exit(f"No service-account key at {CREDS_PATH}\n"
                 f"Follow the ONE-TIME SETUP in this file's docstring, then retry.")
    return Credentials.from_service_account_file(CREDS_PATH, scopes=SCOPES)


def open_sheet():
    gc = gspread.authorize(creds())
    try:
        return gc.open_by_key(SHEET_ID).sheet1
    except gspread.exceptions.APIError as e:
        sys.exit(f"Could not open sheet {SHEET_ID}: {e}\n"
                 f"Did you share it (Editor) with the service-account email? "
                 f"Run:  python sheets_sync.py --whoami")


def grid_rows_from_links(path, by_day):
    rows = []
    if by_day:
        src = build_grid.read_links(path, True)
        for d in build_grid.DAYS:
            print(f"Resolving {len(src[d])} link(s) for {d}…", file=sys.stderr)
            rows += build_grid.pack_day(build_grid.resolve(src[d]), d)
    else:
        pool = build_grid.resolve(build_grid.read_links(path, False))
        print(f"Resolved {len(pool)} videos; packing across all 7 days…", file=sys.stderr)
        for d in build_grid.DAYS:
            rows += build_grid.pack_day(pool, d)
    # pack_day rows are (day, start, video, title, duration)
    return [list(r) for r in rows]


def grid_rows_from_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        r = list(csv.reader(f))
    return r[1:] if r and r[0][:1] == ["day"] else r


def write_grid(rows):
    ws = open_sheet()
    ws.clear()
    ws.update([HEADER] + rows, value_input_option="RAW")
    print(f"Wrote {len(rows)} program rows to sheet {SHEET_ID}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--links", help="links.txt to resolve + pack + write")
    ap.add_argument("--by-day", action="store_true")
    ap.add_argument("--csv", help="prebuilt grid CSV to write")
    ap.add_argument("--read", action="store_true", help="print the sheet's current rows")
    ap.add_argument("--whoami", action="store_true", help="print the service-account email")
    args = ap.parse_args()

    if args.whoami:
        import json
        print(json.load(open(CREDS_PATH))["client_email"])
        return
    if args.read:
        for row in open_sheet().get_all_values():
            print(",".join(row))
        return
    if args.links:
        write_grid(grid_rows_from_links(args.links, args.by_day))
        return
    if args.csv:
        write_grid(grid_rows_from_csv(args.csv))
        return
    ap.error("give --links, --csv, --read, or --whoami")


if __name__ == "__main__":
    main()
