#!/usr/bin/env python3
"""
PYT Radar â Automated podcast & YouTube episode scanner.
Runs as a GitHub Action on a cron schedule.

Searches for new episodes, transcribes via AssemblyAI,
generates summary + transcript docs, uploads to Dropbox,
and updates the GitHub Pages blog + RSS feed.
"""

import os
import sys
import json
import time
import re
import subprocess
import tempfile
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Configuration
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

ASSEMBLYAI_KEY = os.environ["ASSEMBLYAI_API_KEY"]
DROPBOX_TOKEN = os.environ["DROPBOX_TOKEN"]
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]

SEARCH_TARGETS = json.loads(os.environ.get("SEARCH_TARGETS", '{"guests":["Karen Bass"],"topics":["Nithya Raman"]}'))
BLOG_URL = "https://ericspiegelman.github.io/pyt-radar/"
REPO_ROOT = Path(os.environ.get("GITHUB_WORKSPACE", "."))
EPISODES_FILE = REPO_ROOT / "data" / "found_episodes.json"
INDEX_FILE = REPO_ROOT / "index.html"
FEED_FILE = REPO_ROOT / "feed.xml"


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Episode log (deduplication)
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def load_episodes():
    if EPISODES_FILE.exists():
        return json.loads(EPISODES_FILE.read_text())
    return {"episodes": []}


def save_episodes(data):
    EPISODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    EPISODES_FILE.write_text(json.dumps(data, indent=2))


def is_duplicate(url, episodes_data):
    return any(ep["url"] == url for ep in episodes_data["episodes"])


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# YouTube Data API search
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def search_youtube(query, max_results=10, published_after=None):
    """Search YouTube using the Data API v3."""
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "order": "date",
        "key": YOUTUBE_API_KEY,
    }
    if published_after:
        params["publishedAfter"] = published_after.strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    results = []
    for item in data.get("items", []):
        vid = item["id"].get("videoId")
        if not vid:
            continue
        snippet = item["snippet"]
        results.append({
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": snippet["title"],
            "channel": snippet["channelTitle"],
            "published": snippet["publishedAt"],
            "description": snippet.get("description", ""),
        })
    return results


def get_video_duration(video_id):
    """Get video duration in seconds via YouTube Data API."""
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        "part": "contentDetails",
        "id": video_id,
        "key": YOUTUBE_API_KEY,
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    if not items:
        return 0
    duration_str = items[0]["contentDetails"]["duration"]  # e.g. PT1H23M45S
    hours = int(re.search(r'(\d+)H', duration_str).group(1)) if 'H' in duration_str else 0
    minutes = int(re.search(r'(\d+)M', duration_str).group(1)) if 'M' in duration_str else 0
    seconds = int(re.search(r'(\d+)S', duration_str).group(1)) if 'S' in duration_str else 0
    return hours * 3600 + minutes * 60 + seconds


def find_new_episodes(episodes_data):
    """Search for new episodes matching all targets."""
    new_episodes = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)  # 7-day window for robustness

    for guest in SEARCH_TARGETS.get("guests", []):
        print(f"Searching YouTube for guest: {guest}")
        queries = [
            f'"{guest}" interview OR podcast OR guest',
            f'"{guest}" podcast episode',
        ]
        for q in queries:
            results = search_youtube(q, max_results=10, published_after=cutoff)
            for r in results:
                if is_duplicate(r["url"], episodes_data):
                    print(f"  Skip (duplicate): {r['title']}")
                    continue
                # Check if already in our new_episodes list
                if any(e["url"] == r["url"] for e in new_episodes):
                    continue
                # Filter: must be at least 5 minutes (skip shorts/clips)
                duration = get_video_duration(r["video_id"])
                if duration < 300:
                    print(f"  Skip (too short {duration}s): {r['title']}")
                    continue
                # Check if the guest name appears in title or description
                text = (r["title"] + " " + r["description"]).lower()
                if guest.lower() not in text:
                    print(f"  Skip (name not in metadata): {r['title']}")
                    continue
                print(f"  FOUND: {r['title']} ({r['channel']})")
                new_episodes.append({
                    "url": r["url"],
                    "video_id": r["video_id"],
                    "title": r["title"],
                    "show_name": r["channel"],
                    "date_published": r["published"][:10],
                    "search_target": guest,
                    "match_type": "guest",
                    "duration": duration,
                })

    for topic in SEARCH_TARGETS.get("topics", []):
        print(f"Searching YouTube for topic: {topic}")
        queries = [
            f'"{topic}" interview OR podcast OR discussion',
            f'"{topic}" podcast episode',
        ]
        for q in queries:
            results = search_youtube(q, max_results=10, published_after=cutoff)
            for r in results:
                if is_duplicate(r["url"], episodes_data):
                    print(f"  Skip (duplicate): {r['title']}")
                    continue
                if any(e["url"] == r["url"] for e in new_episodes):
                    continue
                duration = get_video_duration(r["video_id"])
                if duration < 300:
                    print(f"  Skip (too short {duration}s): {r['title']}")
                    continue
                text = (r["title"] + " " + r["description"]).lower()
                if topic.lower() not in text:
                    print(f"  Skip (topic not in metadata): {r['title']}")
                    continue
                print(f"  FOUND: {r['title']} ({r['channel']})")
                new_episodes.append({
                    "url": r["url"],
                    "video_id": r["video_id"],
                    "title": r["title"],
                    "show_name": r["channel"],
                    "date_published": r["published"][:10],
                    "search_target": topic,
                    "match_type": "mentioned",
                    "duration": duration,
                })

    return new_episodes


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Transcription (AssemblyAI)
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def download_youtube_audio(url):
    """Download audio from YouTube using yt-dlp, return local file path."""
    tmpdir = tempfile.mkdtemp()
    output_template = os.path.join(tmpdir, "audio.%(ext)s")
    subprocess.run(
        ["yt-dlp", "-f", "bestaudio", "-o", output_template, url],
        check=True, capture_output=True
    )
    for f in os.listdir(tmpdir):
        if f.startswith("audio."):
            return os.path.join(tmpdir, f)
    raise RuntimeError("yt-dlp did not produce an audio file")


def upload_to_assemblyai(filepath):
    """Upload a local audio file to AssemblyAI and return the upload URL."""
    print("  Uploading audio to AssemblyAI...")
    with open(filepath, "rb") as f:
        resp = requests.post(
            "https://api.assemblyai.com/v2/upload",
            headers={"Authorization": ASSEMBLYAI_KEY},
            data=f
        )
    resp.raise_for_status()
    return resp.json()["upload_url"]


def transcribe(audio_url):
    """Submit transcription and poll until complete. Returns transcript dict."""
    print("  Submitting transcription request...")
    resp = requests.post(
        "https://api.assemblyai.com/v2/transcript",
        headers={
            "Authorization": ASSEMBLYAI_KEY,
            "Content-Type": "application/json",
        },
        json={
            "audio_url": audio_url,
            "speaker_labels": True,
            "auto_highlights": True,
            "speech_models": ["universal-3-pro"],
        }
    )
    resp.raise_for_status()
    transcript_id = resp.json()["id"]
    print(f"  Transcript ID: {transcript_id}")

    while True:
        resp = requests.get(
            f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
            headers={"Authorization": ASSEMBLYAI_KEY}
        )
        resp.raise_for_status()
        data = resp.json()
        status = data["status"]

        if status == "completed":
            print("  Transcription complete!")
            return data
        elif status == "error":
            raise RuntimeError(f"Transcription failed: {data.get('error', 'Unknown')}")

        print(f"  Status: {status} â waiting 15s...")
        time.sleep(15)


def transcribe_episode(episode):
    """Full pipeline: download YouTube audio â upload â transcribe."""
    print(f"Transcribing: {episode['title']}")
    audio_path = download_youtube_audio(episode["url"])
    try:
        upload_url = upload_to_assemblyai(audio_path)
    finally:
        os.unlink(audio_path)
        os.rmdir(os.path.dirname(audio_path))
    return transcribe(upload_url)


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Summary generation (using Claude API)
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def generate_summary_with_claude(episode, transcript_data):
    """Use Claude API to generate a structured summary of the transcript."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  WARNING: No ANTHROPIC_API_KEY â generating basic summary")
        return generate_basic_summary(episode, transcript_data)

    # Build a condensed version of the transcript for Claude
    utterances = transcript_data.get("utterances", [])
    transcript_text = ""
    for u in utterances:
        start_sec = u["start"] // 1000
        mm, ss = divmod(start_sec, 60)
        transcript_text += f"[{mm:02d}:{ss:02d}] Speaker {u['speaker']}: {u['text']}\n"

    # Truncate if too long (keep first and last portions)
    if len(transcript_text) > 80000:
        half = 38000
        transcript_text = transcript_text[:half] + "\n\n[...TRUNCATED...]\n\n" + transcript_text[-half:]

    prompt = f"""Analyze this podcast/YouTube transcript and generate a structured summary.

EPISODE INFO:
- Title: {episode['title']}
- Show: {episode['show_name']}
- Search target: {episode['search_target']} ({episode['match_type']})
- URL: {episode['url']}

TRANSCRIPT:
{transcript_text}

Generate a JSON response with this exact structure:
{{
  "overview": "2-3 sentence overview of what the episode is about and why the search target is relevant",
  "topics": ["topic 1", "topic 2", ...],
  "sentiment": "positive|negative|neutral|mixed",
  "sentiment_explanation": "2-3 sentences explaining the tone toward the search target with specific examples",
  "key_quotes": [
    {{"text": "the quote", "speaker": "Speaker Name or Speaker X", "timestamp_mm_ss": "MM:SS", "timestamp_seconds": 1234}},
    ...
  ],
  "key_moment": {{
    "text": "the single most important quote about the search target",
    "speaker": "Speaker Name",
    "timestamp_mm_ss": "MM:SS",
    "timestamp_seconds": 1234,
    "context": "1 sentence explaining why this moment matters"
  }},
  "sections": [
    {{"start": "MM:SS", "end": "MM:SS", "description": "what was discussed"}},
    ...
  ]
}}

IMPORTANT: For timestamps, provide both MM:SS format and total seconds. Identify speakers by name if possible based on context (show hosts, known guests), otherwise use Speaker A/B/C labels."""

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
    )
    resp.raise_for_status()
    content = resp.json()["content"][0]["text"]

    # Extract JSON from response
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        return json.loads(json_match.group())
    else:
        print("  WARNING: Could not parse Claude response, using basic summary")
        return generate_basic_summary(episode, transcript_data)


def generate_basic_summary(episode, transcript_data):
    """Fallback summary when Claude API is not available."""
    utterances = transcript_data.get("utterances", [])
    duration_sec = transcript_data.get("audio_duration", 0)
    duration_min = duration_sec // 60

    # Find utterances mentioning the search target
    target = episode["search_target"].lower()
    matching = []
    for u in utterances:
        if target in u["text"].lower():
            start_sec = u["start"] // 1000
            mm, ss = divmod(start_sec, 60)
            matching.append({
                "text": u["text"],
                "speaker": f"Speaker {u['speaker']}",
                "timestamp_mm_ss": f"{mm:02d}:{ss:02d}",
                "timestamp_seconds": start_sec,
            })

    key_moment = matching[0] if matching else {
        "text": "Search target mentioned in this episode",
        "speaker": "Unknown",
        "timestamp_mm_ss": "00:00",
        "timestamp_seconds": 0,
        "context": "Automated detection â manual review recommended",
    }
    if "context" not in key_moment:
        key_moment["context"] = f"{episode['search_target']} was discussed in this episode"

    return {
        "overview": f"This {duration_min}-minute episode of {episode['show_name']} mentions {episode['search_target']}.",
        "topics": [episode["search_target"], episode["show_name"]],
        "sentiment": "neutral",
        "sentiment_explanation": "Automated analysis â sentiment could not be determined without Claude API.",
        "key_quotes": matching[:5],
        "key_moment": key_moment,
        "sections": [{"start": "00:00", "end": f"{duration_min}:00", "description": "Full episode"}],
    }


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Document generation (.docx)
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def create_summary_docx(episode, summary, output_path):
    """Create a summary .docx file. Uses python-docx."""
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # Title
    doc.add_heading("PYT Radar â Episode Summary", level=1)
    doc.add_paragraph("")

    # Metadata
    meta_fields = [
        ("Episode", episode["title"]),
        ("Show", episode["show_name"]),
        ("Date Found", datetime.now().strftime("%B %d, %Y")),
        ("Published", episode["date_published"]),
        ("URL", episode["url"]),
        ("Search Target", f"{episode['search_target']} ({episode['match_type']})"),
    ]
    for label, value in meta_fields:
        p = doc.add_paragraph()
        run = p.add_run(f"{label}: ")
        run.bold = True
        p.add_run(value)

    doc.add_paragraph("")

    # Overview
    doc.add_heading("Overview", level=2)
    doc.add_paragraph(summary["overview"])

    # Topics
    doc.add_heading("Topics Covered", level=2)
    for topic in summary.get("topics", []):
        doc.add_paragraph(topic, style="List Bullet")

    # Sentiment
    doc.add_heading("Sentiment Analysis", level=2)
    p = doc.add_paragraph()
    run = p.add_run(f"Sentiment toward {episode['search_target']}: {summary['sentiment'].upper()}")
    run.bold = True
    doc.add_paragraph(summary.get("sentiment_explanation", ""))

    # Key Quotes
    doc.add_heading("Key Quotes", level=2)
    for i, q in enumerate(summary.get("key_quotes", []), 1):
        ts = q.get("timestamp_mm_ss", "??:??")
        secs = q.get("timestamp_seconds", 0)
        speaker = q.get("speaker", "Unknown")
        p = doc.add_paragraph()
        run = p.add_run(f"{i}. [{ts} / {secs}s] {speaker}: ")
        run.bold = True
        p.add_run(f'"{q["text"]}"')

    # Key Moment
    km = summary.get("key_moment", {})
    if km:
        doc.add_heading("Key Moment", level=2)
        p = doc.add_paragraph()
        run = p.add_run("Timestamp: ")
        run.bold = True
        p.add_run(f"{km.get('timestamp_mm_ss', '??:??')} ({km.get('timestamp_seconds', 0)} seconds)")

        p = doc.add_paragraph()
        run = p.add_run("Speaker: ")
        run.bold = True
        p.add_run(km.get("speaker", "Unknown"))

        p = doc.add_paragraph()
        run = p.add_run("Quote: ")
        run.bold = True
        p.add_run(f'"{km.get("text", "")}"')

        yt_link = f"{episode['url']}&t={km.get('timestamp_seconds', 0)}"
        p = doc.add_paragraph()
        run = p.add_run("YouTube timecoded link: ")
        run.bold = True
        p.add_run(yt_link)

    doc.save(output_path)
    print(f"  Summary saved: {output_path}")


def create_transcript_docx(episode, transcript_data, output_path):
    """Create a transcript .docx file."""
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    doc.add_heading(episode["title"], level=1)
    doc.add_paragraph("")

    meta_fields = [
        ("Show", episode["show_name"]),
        ("Date Published", episode["date_published"]),
        ("Duration", f"{transcript_data.get('audio_duration', 0) // 60} minutes"),
        ("URL", episode["url"]),
        ("Transcribed by", "AssemblyAI (speaker diarization enabled)"),
    ]
    for label, value in meta_fields:
        p = doc.add_paragraph()
        run = p.add_run(f"{label}: ")
        run.bold = True
        p.add_run(value)

    doc.add_paragraph("")
    doc.add_heading("Transcript", level=2)
    doc.add_paragraph("")

    for u in transcript_data.get("utterances", []):
        start_sec = u["start"] // 1000
        mm, ss = divmod(start_sec, 60)
        p = doc.add_paragraph()
        ts_run = p.add_run(f"[{mm:02d}:{ss:02d}] ")
        ts_run.font.color.rgb = RGBColor(128, 128, 128)
        ts_run.font.size = Pt(9)
        speaker_run = p.add_run(f"Speaker {u['speaker']}: ")
        speaker_run.bold = True
        p.add_run(u["text"])

    doc.save(output_path)
    print(f"  Transcript saved: {output_path}")


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Dropbox upload + shared links
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def upload_to_dropbox(local_path, dropbox_path):
    """Upload a file to Dropbox."""
    print(f"  Uploading to Dropbox: {dropbox_path}")
    with open(local_path, "rb") as f:
        resp = requests.post(
            "https://content.dropboxapi.com/2/files/upload",
            headers={
                "Authorization": f"Bearer {DROPBOX_TOKEN}",
                "Dropbox-API-Arg": json.dumps({
                    "path": dropbox_path,
                    "mode": "overwrite",
                    "autorename": True,
                    "mute": False,
                }),
                "Content-Type": "application/octet-stream",
            },
            data=f,
        )
    resp.raise_for_status()
    result = resp.json()
    if "error_summary" in result:
        raise RuntimeError(f"Dropbox upload failed: {result['error_summary']}")
    print(f"  Uploaded: {result.get('path_display')}")


def get_dropbox_link(dropbox_path):
    """Get or create a shared link for a Dropbox file."""
    resp = requests.post(
        "https://api.dropboxapi.com/2/sharing/create_shared_link_with_settings",
        headers={
            "Authorization": f"Bearer {DROPBOX_TOKEN}",
            "Content-Type": "application/json",
        },
        json={
            "path": dropbox_path,
            "settings": {"requested_visibility": "public"},
        }
    )
    data = resp.json()
    error = data.get("error_summary", "")

    if "shared_link_already_exists" in error:
        resp2 = requests.post(
            "https://api.dropboxapi.com/2/sharing/list_shared_links",
            headers={
                "Authorization": f"Bearer {DROPBOX_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"path": dropbox_path, "direct_only": True},
        )
        resp2.raise_for_status()
        links = resp2.json().get("links", [])
        if links:
            return links[0]["url"]
        raise RuntimeError(f"No shared links found for {dropbox_path}")
    elif error:
        raise RuntimeError(f"Could not create shared link: {error}")
    else:
        return data["url"]


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Blog + RSS feed updates
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def make_slug(show_name):
    """Create a URL-friendly slug from a show name."""
    return re.sub(r'[^a-z0-9]+', '-', show_name.lower()).strip('-')


def html_escape(text):
    """Basic HTML escaping."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def update_blog(episode, summary, dropbox_links):
    """Add a new digest entry to index.html."""
    today = datetime.now().strftime("%B %d, %Y")
    date_slug = datetime.now().strftime("%Y-%m-%d")
    show_slug = make_slug(episode["show_name"])
    entry_id = f"{date_slug}-{show_slug}"

    km = summary.get("key_moment", {})
    km_ts = km.get("timestamp_mm_ss", "0:00")
    km_secs = km.get("timestamp_seconds", 0)
    km_text = html_escape(km.get("text", ""))
    km_speaker = html_escape(km.get("speaker", "Unknown"))

    sentiment_class = f"sentiment-{summary.get('sentiment', 'neutral')}"
    match_class = "tag-guest" if episode["match_type"] == "guest" else "tag-mention"
    match_label = "Guest" if episode["match_type"] == "guest" else "Mentioned"

    digest_html = f"""    <div class="digest" id="{entry_id}">
      <div class="digest-date">{today}</div>
      <h2>{html_escape(episode['show_name'])}</h2>
      <h3>{html_escape(episode['title'])}</h3>
      <div class="tags">
        <span class="tag">{html_escape(episode['search_target'])}</span>
        <span class="tag {match_class}">{match_label}</span>
      </div>
      <div class="sentiment {sentiment_class}">Sentiment: {summary.get('sentiment', 'neutral').title()}</div>
      <p>{html_escape(summary.get('overview', ''))}</p>
      <blockquote class="quote">
        <p>&ldquo;{km_text}&rdquo;</p>
        <cite>&mdash; {km_speaker} at <a href="{episode['url']}&amp;t={km_secs}">{km_ts}</a></cite>
      </blockquote>
      <div class="links">
        <a href="{episode['url']}">&#9654; Watch Episode</a> |
        <a href="{html_escape(dropbox_links['summary'])}">&#128196; Summary</a> |
        <a href="{html_escape(dropbox_links['transcript'])}">&#128196; Full Transcript</a>
      </div>
    </div>"""

    # Read current index.html and insert after <main id="digests">
    content = INDEX_FILE.read_text()

    # Remove empty placeholder if present
    content = re.sub(r'<p class="empty">.*?</p>\s*', '', content)

    # Insert new entry after <main id="digests">
    insertion_point = '<main id="digests">'
    if insertion_point in content:
        content = content.replace(insertion_point, insertion_point + "\n" + digest_html)
    else:
        print("  WARNING: Could not find insertion point in index.html")
        return

    INDEX_FILE.write_text(content)
    print(f"  Blog updated with entry: {entry_id}")
    return entry_id


def update_rss(episode, summary, dropbox_links, entry_id):
    """Add a new item to feed.xml."""
    now = datetime.now(timezone.utc)
    pub_date = now.strftime("%a, %d %b %Y %H:%M:%S +0000")

    km = summary.get("key_moment", {})
    km_ts = km.get("timestamp_mm_ss", "0:00")
    km_secs = km.get("timestamp_seconds", 0)

    headline = f"{episode['show_name'].split(' with ')[0] if ' with ' in episode['show_name'] else episode['show_name']}: {summary.get('overview', episode['title'])[:80]}"

    description_html = f"""<p><strong>{html_escape(episode['show_name'])}</strong><br/>
"{html_escape(episode['title'])}" â Published {episode['date_published']}</p>
<p><strong>Match:</strong> {html_escape(episode['search_target'])} ({episode['match_type']})</p>
<p><strong>Sentiment: {summary.get('sentiment', 'neutral').title()}</strong></p>
<p>{html_escape(summary.get('overview', ''))}</p>
<p><strong>Key moment at <a href="{episode['url']}&t={km_secs}">{km_ts}</a>:</strong> "{html_escape(km.get('text', ''))}" â {html_escape(km.get('speaker', 'Unknown'))}</p>
<p><a href="{episode['url']}">â¶ Watch Episode</a> |
<a href="{dropbox_links['summary']}">ð Summary</a> |
<a href="{dropbox_links['transcript']}">ð Full Transcript</a></p>"""

    item_xml = f"""    <item>
      <title>{html_escape(headline)}</title>
      <link>{BLOG_URL}#{entry_id}</link>
      <guid>{BLOG_URL}#{entry_id}</guid>
      <pubDate>{pub_date}</pubDate>
      <description><![CDATA[{description_html}]]></description>
    </item>"""

    content = FEED_FILE.read_text()

    # Insert after the <atom:link> line
    atom_link_pattern = r'(<atom:link[^/]*/>\s*)'
    match = re.search(atom_link_pattern, content)
    if match:
        insert_pos = match.end()
        content = content[:insert_pos] + "\n" + item_xml + "\n" + content[insert_pos:]
    else:
        # Fallback: insert before </channel>
        content = content.replace("</channel>", item_xml + "\n  </channel>")

    FEED_FILE.write_text(content)
    print(f"  RSS feed updated")


# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
# Main pipeline
# ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

def process_episode(episode, episodes_data):
    """Full pipeline for a single episode."""
    print(f"\n{'='*60}")
    print(f"Processing: {episode['title']}")
    print(f"  Show: {episode['show_name']}")
    print(f"  Target: {episode['search_target']} ({episode['match_type']})")
    print(f"{'='*60}")

    # 1. Transcribe
    transcript_data = transcribe_episode(episode)

    # 2. Generate summary
    print("Generating summary...")
    summary = generate_summary_with_claude(episode, transcript_data)

    # 3. Create .docx files
    safe_title = re.sub(r'[^\w\s-]', '', episode['title']).strip()
    safe_show = re.sub(r'[^\w\s-]', '', episode['show_name']).strip()
    base_name = f"{safe_show} - {safe_title}"

    tmpdir = tempfile.mkdtemp()
    summary_path = os.path.join(tmpdir, f"{base_name} - Summary.docx")
    transcript_path = os.path.join(tmpdir, f"{base_name} - Transcript.docx")

    create_summary_docx(episode, summary, summary_path)
    create_transcript_docx(episode, transcript_data, transcript_path)

    # 4. Upload to Dropbox
    dbx_summary = f"/{base_name} - Summary.docx"
    dbx_transcript = f"/{base_name} - Transcript.docx"

    upload_to_dropbox(summary_path, dbx_summary)
    upload_to_dropbox(transcript_path, dbx_transcript)

    # 5. Get shared links
    summary_link = get_dropbox_link(dbx_summary)
    transcript_link = get_dropbox_link(dbx_transcript)
    print(f"  Summary link: {summary_link}")
    print(f"  Transcript link: {transcript_link}")

    dropbox_links = {
        "summary": summary_link,
        "transcript": transcript_link,
    }

    # 6. Update blog and RSS
    entry_id = update_blog(episode, summary, dropbox_links)
    if entry_id:
        update_rss(episode, summary, dropbox_links, entry_id)

    # 7. Update episode log
    episodes_data["episodes"].append({
        "url": episode["url"],
        "title": episode["title"],
        "show_name": episode["show_name"],
        "date_found": datetime.now().strftime("%Y-%m-%d"),
        "date_published": episode["date_published"],
        "search_target": episode["search_target"],
        "match_type": episode["match_type"],
        "dropbox_paths": {
            "transcript": dbx_transcript,
            "summary": dbx_summary,
        },
        "dropbox_links": dropbox_links,
    })
    save_episodes(episodes_data)

    # Cleanup
    os.unlink(summary_path)
    os.unlink(transcript_path)
    os.rmdir(tmpdir)

    print(f"\nDone processing: {episode['title']}")
    return True


def main():
    print("=" * 60)
    print("PYT Radar â Scan starting")
    print(f"Time: {datetime.now().isoformat()}")
    print("=" * 60)

    # Load episode log
    episodes_data = load_episodes()
    print(f"Loaded {len(episodes_data['episodes'])} previously found episodes")

    # Search for new episodes
    new_episodes = find_new_episodes(episodes_data)

    if not new_episodes:
        print("\nNo new episodes found. Scan complete.")
        return

    print(f"\nFound {len(new_episodes)} new episode(s) to process")

    # Process each new episode
    processed = 0
    for episode in new_episodes:
        try:
            if process_episode(episode, episodes_data):
                processed += 1
        except Exception as e:
            print(f"\nERROR processing {episode['title']}: {e}")
            continue

    print(f"\n{'='*60}")
    print(f"Scan complete. Processed {processed}/{len(new_episodes)} episodes.")
    print(f"{'='*60}")

    # Set output for GitHub Actions
    if processed > 0:
        github_output = os.environ.get("GITHUB_OUTPUT", "")
        if github_output:
            with open(github_output, "a") as f:
                f.write(f"episodes_found={processed}\n")


if __name__ == "__main__":
    main()
