# FAST Channel

A lightweight, single-file template that turns a list of YouTube videos into a
linear **"live TV" channel** — viewers tune in and land mid-stream at the same
spot, like a real broadcast, and the lineup loops forever.

Everything is in [`index.html`](index.html). No build step, no dependencies.

## How the "live" part works

The channel doesn't just autoplay a playlist from the top. It computes what
*should* be on right now from the wall clock:

```
position_in_loop = (now − EPOCH_ANCHOR) mod total_runtime
```

So whoever opens the page joins the program already in progress. When a program
ends, the channel re-checks the clock and jumps to whatever is live then — which
self-corrects any drift between a video's stated and real length.

This is why every row needs a **duration**: it's how the channel knows when each
program starts and ends without loading every video first.

## Set up your channel (Google Sheet)

1. Create a Google Sheet with a header row and these columns (any order):

   | video | title | duration |
   |-------|-------|----------|
   | `dQw4w9WgXcQ` | Opening Block | `3:33` |
   | `https://youtu.be/aqz-KE-bpKQ` | Big Buck Bunny | `10:34` |
   | `https://www.youtube.com/watch?v=...` | Episode 2 | `754` |

   - **video** — YouTube URL (`watch?v=`, `youtu.be/`, `/embed/`, `/shorts/`) or bare 11-char ID
   - **title** — shown in the lower-third and guide (optional)
   - **duration** — `h:mm:ss`, `m:ss`, or plain seconds — **required**

2. **File → Share → Publish to web → (this sheet) → Comma-separated values (.csv)**.
3. Paste that published URL into `SHEET_CSV_URL` at the top of `index.html`.

Until you do step 3, the page runs a built-in **demo** lineup so you can see it
working immediately. Edit the sheet anytime — the page re-fetches every few
minutes (`REFRESH_MIN`).

## Config (top of `index.html`)

| Key | What it does |
|-----|--------------|
| `CHANNEL_NAME` | Name in the channel bug and guide |
| `SHEET_CSV_URL` | Your published-CSV link (empty = demo lineup) |
| `EPOCH_ANCHOR` | Fixed instant the schedule counts from — don't change once live |
| `REFRESH_MIN` | How often to re-fetch the sheet for edits |
| `HIDE_LOWER_S` | Seconds before the now/next lower-third auto-hides |

## Viewer controls

- **Tap to tune in** — unmutes (browsers block unmuted autoplay, so it starts muted)
- **📺 Guide** (`g`) — on-screen program guide with start times
- **🔇 / 🔊** (`m`) — toggle sound
- Tap the picture — briefly reveal now-playing / up-next

## Deploy

It's one static file — host it anywhere: GitHub Pages, Netlify, S3, or open
`index.html` locally. No server code required.

## Notes & limits

- Uses the YouTube IFrame Player API. Videos whose owners disable embedding
  can't play; the channel skips them automatically.
- The picture is scaled to fill the screen (TV-style crop), so very wide/tall
  windows crop the edges rather than letterboxing.
- Pure client-side: the published-CSV link is visible in page source. Don't put
  anything private in the sheet.
