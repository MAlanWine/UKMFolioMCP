# UKMFolio MCP Server

**🌐 语言 / Language:** **English** · [中文](./README.zh-CN.md)

An [MCP](https://modelcontextprotocol.io) (Model Context Protocol) server that lets AI assistants (Claude Desktop / Claude Code, etc.) directly access the content of the currently logged-in student on **UKM Folio** — the Moodle learning platform of Universiti Kebangsaan Malaysia (UKM):

- 📚 Enrolled courses
- 📝 Assignment / quiz deadlines
- 📢 Forum announcements and discussions
- 📄 Course documents (PDF / PPTX / DOCX / XLSX …, **auto-downloaded with body text extracted**)
- 🖼️ Images teachers post (announcement posters, QR codes, schedules …) — **flagged in every result and returned as real images for the AI to read**

The login and data-access logic is carried over from the battle-tested [`UKMFolioPuller`](./UKMFolioPuller): it first performs **SAML 2.0 single sign-on** through `sso.ukm.my`, then calls Moodle's AJAX endpoints plus targeted HTML scraping. (UKM Folio disables most list-level Web Service functions, so this hybrid approach is the one proven to actually work.)

> ⚠️ Read-only tools — they will never submit assignments, post to forums, or change grades on your behalf.

---

## Table of Contents

- [Features & Tools at a Glance](#features--tools-at-a-glance)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuring config.json](#configuring-configjson)
- [Quick Start: Self-Check](#quick-start-self-check)
- [Running the Server](#running-the-server)
- [Connecting an AI Client](#connecting-an-ai-client)
- [Tool Reference](#tool-reference)
- [Typical Use Cases](#typical-use-cases)
- [Deploying to a Server (long-running)](#deploying-to-a-server-long-running)
- [Troubleshooting](#troubleshooting)
- [Project Structure & How It Works](#project-structure--how-it-works)
- [Security & Limitations](#security--limitations)

---

## Features & Tools at a Glance

The server exposes 11 tools:

| Tool | What it does |
|------|------|
| `list_courses` | List enrolled courses (id, full name, course code, category, progress, link) |
| `list_deadlines` | List assignment/quiz deadlines, filterable by course, days ahead, and whether to include past ones |
| `get_submission_status` | Check whether assignments are submitted / graded / overdue (per course, or one by cmid) |
| `list_announcements` | List forum announcements/discussions, newest first |
| `get_discussion` | Fetch the full post thread of one discussion (body HTML + plain text) |
| `list_documents` | List a course's documents (resource/folder/url/page/book), giving each `cmid` and `type` |
| `read_document` | Download a document and **extract its body text** (PDF/DOCX/PPTX/XLSX/TXT/HTML) |
| `list_modules` | List every kind of activity module in a course (to discover a cmid / inspect course structure) |
| `list_images` | List every image teachers posted in a course (forum posts, labels, sections, pages, image files), each with an `image_url` |
| `view_image` | Fetch one image by `image_url` and **return the image itself** so the AI can read it (auto-downscaled) |
| `whoami` | Diagnostics: confirm login succeeded; returns site, timezone, course count |

**Conventions shared across tools:**

- Most tools take an optional `course` filter, which accepts a **course id**, a **course code** (e.g. `TTTN2423`), or **any substring of the course name**. If omitted, the tool covers all enrolled courses.
- Time fields are returned in two forms: **unix seconds** (e.g. `deadline`) and an **ISO-8601 local-time string** (e.g. `deadline_local`, defaulting to Malaysia time `Asia/Kuala_Lumpur`).
- The standard document-reading flow: call `list_documents` first to obtain `cmid` and `type`, then `read_document(cmid, type)`.
- **Images are identified by their URL.** Wherever an image appears, results carry an `images` list (each entry has an `image_url`), post text marks its position inline as `[image: <alt> | <image_url>]`, and files that are images get `is_image: true`. Pass the URL to `view_image` to see it.

---

## Requirements

- **Python ≥ 3.11** (this machine uses `/home/alanwine/PyVenv`, Python 3.14)
- Network access to `ukmfoliov2.ukm.my` and `sso.ukm.my`
- A valid UKM Folio account (matric number + password)

Dependencies (declared in `requirements.txt` / `pyproject.toml`):

```
mcp            # Official MCP Python SDK (FastMCP)
requests       # HTTP / session
pypdf          # Extract PDF text
python-docx    # Extract .docx
python-pptx    # Extract .pptx
openpyxl       # Extract .xlsx
beautifulsoup4 # Parse HTML pages
lxml           # bs4 parsing backend
pillow         # Decode / downscale / convert images for view_image
```

> `mcp` is pinned to `<2`: mcp 2.x renamed `FastMCP`, which this server uses.

---

## Installation

```bash
# 1) Enter the project directory
cd /home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP

# 2) Install dependencies (pick one)
/home/alanwine/PyVenv/bin/pip install -r requirements.txt
#   or install as an "editable package", which also provides the ukmfolio-mcp CLI entry point
/home/alanwine/PyVenv/bin/pip install -e .

# 3) Prepare the config file
cp config.example.json config.json
# then edit config.json and fill in your matric number and password
```

> After `pip install -e .`, every `python -m ukmfolio_mcp` below can be shortened to `ukmfolio-mcp`.

---

## Configuring config.json

`config.json` uses the same field set as `UKMFolioPuller`, but here only the login-related fields are required (any Telegram fields, if present, are ignored).

```json
{
    "username": "a207421",
    "password": "your_password",
    "base_url": "https://ukmfoliov2.ukm.my",
    "sso_url": "https://sso.ukm.my",
    "timezone": "Asia/Kuala_Lumpur",
    "host": "127.0.0.1",
    "port": 8000
}
```

| Field | Required | Description |
|------|:---:|------|
| `username` | ✅ | UKM matric number |
| `password` | ✅ | Login password |
| `base_url` | | UKM Folio site, defaults to `https://ukmfoliov2.ukm.my` (the old `ukmfolio.ukm.my` now redirects there) |
| `sso_url` | | SSO site, defaults to `https://sso.ukm.my` |
| `timezone` | | Timezone used for time fields, defaults to `Asia/Kuala_Lumpur` |
| `host` | | Default listen address in HTTP mode, defaults to `127.0.0.1` (overridable via `--host`) |
| `port` | | Default port in HTTP mode, defaults to `8000` (overridable via `--port`) |

**Credential / config-file lookup order:**

- Credentials: the environment variables `UKMFOLIO_USERNAME` / `UKMFOLIO_PASSWORD` take precedence over the file (handy for server deployments so passwords never hit disk in plaintext).
- Config-file path: `--config <path>` → env var `UKMFOLIO_CONFIG` → `config.json` in the project root → `config.json` in the current working directory.

> 🔒 `config.json` is already in `.gitignore` and will not be committed.

---

## Quick Start: Self-Check

The first time you use it, run `--check`. It performs a real login once and prints your course count and upcoming-deadline count, confirming both your account and network are fine. (It does not start the server — it runs and exits.)

```bash
/home/alanwine/PyVenv/bin/python -m ukmfolio_mcp --check
```

Normal output looks like:

```
[*] Logging in to UKM Folio …
[*] Authenticated. base_url=https://ukmfoliov2.ukm.my sesskey=LvH2dF34SK tz=Asia/Kuala_Lumpur
[*] 6 enrolled courses:
        15292  TTTN2423   Keperluan Pensuisan, Penghalaan dan Tanpa Wayar
        23521  TTTM2213   PENGATURCARAAN APLIKASI MUDAH ALIH
        ...
[*] 9 deadlines in next 14 days.
[*] OK
```

---

## Running the Server

```bash
# Local stdio transport (default; for Claude Desktop / Claude Code)
/home/alanwine/PyVenv/bin/python -m ukmfolio_mcp --stdio

# Remote streamable-HTTP transport (deploy to a server, multiple clients)
/home/alanwine/PyVenv/bin/python -m ukmfolio_mcp --http-server --host 0.0.0.0 --port 8000
#   Endpoint: http://<host>:<port>/mcp
```

| Flag | Description |
|------|------|
| `--stdio` | Communicate over standard input/output (default). The MCP client spawns this process itself. |
| `--http-server` | Start a streamable-HTTP (SSE) service with its endpoint at `/mcp`. |
| `--host` / `--port` | Override the listen address / port from config.json (only meaningful in HTTP mode). |
| `--config <path>` | Specify the config-file path. |
| `--check` | Only test login and print stats, then exit. |

> Don't "run and wait" on stdio mode manually — it has no UI and is meant to be driven over a pipe by a client. In normal use you just configure the client, and it launches the server on demand.

---

## Connecting an AI Client

### Claude Code

```bash
claude mcp add ukmfolio -- /home/alanwine/PyVenv/bin/python -m ukmfolio_mcp \
  --stdio --config /home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP/config.json
```

Once added, `/mcp` in Claude Code shows `ukmfolio` and its tools.

### Claude Desktop

Edit `claude_desktop_config.json` (on macOS at `~/Library/Application Support/Claude/`, on Windows at `%APPDATA%\Claude\`) and add:

```json
{
  "mcpServers": {
    "ukmfolio": {
      "command": "/home/alanwine/PyVenv/bin/python",
      "args": [
        "-m", "ukmfolio_mcp", "--stdio",
        "--config", "/home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP/config.json"
      ],
      "cwd": "/home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP"
    }
  }
}
```

Restart Claude Desktop after saving.

> 💡 Strongly prefer an **absolute path** for `--config`, because the working directory of the subprocess a client spawns is not necessarily the project directory — otherwise it may fail to find `config.json`.

### Remote HTTP client

Start the server in `--http-server` mode (see above), then point any MCP client that supports streamable-HTTP at `http://<host>:<port>/mcp`.

---

## Tool Reference

Each tool below lists its **parameters** and a **real example return** (field names match the actual output; values are illustrative). All returns are JSON.

### 1. `list_courses`

List enrolled courses. No parameters.

```jsonc
[
  {
    "course_id": 15292,
    "course_name": "Keperluan Pensuisan, Penghalaan dan Tanpa Wayar",
    "course_shortname": "TTTN2423",
    "course_category": "Fakulti ...",
    "course_url": "https://ukmfolio.ukm.my/course/view.php?id=15292",
    "progress": null
  }
]
```

### 2. `list_deadlines`

List assignment/quiz deadlines.

| Parameter | Type | Default | Description |
|------|------|------|------|
| `course` | string? | all | Course id / code / name substring |
| `days_ahead` | int? | unlimited | Only show deadlines within this many days ahead |
| `include_past` | bool | `true` | Whether to include past-due ones; set `false` for upcoming only |

```jsonc
[
  {
    "item_id": 123456,
    "item_type": "assign",
    "item_title": "[Group 2IT2] Lab 8: WLAN Configurations",
    "deadline": 1782489540,
    "item_url": "https://ukmfolio.ukm.my/mod/assign/view.php?id=...",
    "course_id": 15292,
    "course_shortname": "TTTN2423",
    "course_name": "Keperluan Pensuisan, ...",
    "deadline_local": "2026-06-18T23:59:00+08:00",
    "days_left": 4.3
  }
]
```

### 3. `get_submission_status`

Check whether assignments have been submitted, graded, or are overdue. UKM Folio disables the assignment web-service APIs, so this scrapes each assignment's "Submission status" page — **one HTTP request per assignment**, so prefer a `course` filter or a single `cmid`.

| Parameter | Type | Default | Description |
|------|------|------|------|
| `course` | string? | all | Course filter (id, code, or name substring) — scans every assignment in the matched course(s) |
| `cmid` | int? | — | Check a single assignment by its course-module id (the id in an assignment's `item_url` from `list_deadlines`, or from `list_modules`). Takes precedence over `course`. |

```jsonc
[
  {
    "cmid": 781045,
    "name": "[Group 2AKIT1] Lab 1: Basic Switch and Router Configurations",
    "item_type": "assign",
    "item_url": "https://ukmfolio.ukm.my/mod/assign/view.php?id=781045",
    "submission_status": "Submitted for grading",
    "submitted": true,
    "grading_status": "Not graded",
    "graded": false,
    "time_remaining": "Assignment was submitted 6 hours 59 mins early",
    "is_overdue": false,
    "last_modified": null,
    "grade": null,
    "status_found": true,
    "section": "Topic 1",
    "course_id": 15292,
    "course_shortname": "TTTN2423",
    "course_name": "Keperluan Pensuisan, ..."
  }
]
```

Field notes:

- `submission_status` / `grading_status` / `time_remaining` are the **raw Moodle strings** (e.g. `"No submissions have been made yet"`, `"Not graded"`, `"4 days 4 hours remaining"`).
- `submitted`, `graded`, `is_overdue` are normalized booleans derived from those strings for easy filtering. They are `null` when the page format is unrecognized — in which case `status_found` is `false`.
- The assignment list comes from `core_courseformat_get_state`, so it includes **every** assignment module visible in the course (more complete than `list_deadlines`, which only shows items with a calendar deadline).

### 4. `list_announcements`

List forum announcements/discussions (newest first).

| Parameter | Type | Default | Description |
|------|------|------|------|
| `course` | string? | all | Course filter |
| `limit` | int | `20` | Maximum number of items to return |
| `with_body` | bool | `true` | Whether to include the root post's plain-text body |

```jsonc
[
  {
    "item_id": 317111,
    "item_type": "forum",
    "item_title": "Week 11 Lecture and Lab Challenge 4",
    "author": "DR. WAN FARIZA BINTI PAIZI @ FAUZI",
    "posted_at": 1780000026,
    "posted_at_local": "2026-06-04T10:00:26+08:00",
    "reply_count": 0,
    "item_url": "https://ukmfolio.ukm.my/mod/forum/discuss.php?d=317111",
    "item_body": "Dear Students, Below is the link to today's lecture ...",
    "images": [],
    "course_id": 15292,
    "course_shortname": "TTTN2423",
    "course_name": "Keperluan Pensuisan, ..."
  }
]
```

> `item_id` is the discussion id; pass it to `get_discussion` to see the full thread.

`images` lists every image posted anywhere in the thread (embedded or attached), and `item_body` marks each one inline. Announcements are often *only* a poster, so the text alone can be empty or misleading:

```jsonc
{
  "item_title": "Welcome & Important Notice: No Tutorial/Lab This Week",
  "item_body": "[image: Announcement for no tutorial and lab for this week. | https://ukmfoliov2.ukm.my/pluginfile.php/14955/mod_forum/post/287/Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg]",
  "images": [
    {
      "image_url": "https://ukmfoliov2.ukm.my/pluginfile.php/14955/mod_forum/post/287/Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg",
      "filename": "Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg",
      "alt": "Announcement for no tutorial and lab for this week.",
      "post_id": 287
    }
  ]
}
```

### 5. `get_discussion`

Fetch all posts of a single discussion.

| Parameter | Type | Description |
|------|------|------|
| `discussion_id` | int | Discussion id (from `list_announcements`' `item_id`) |

```jsonc
{
  "discussion_id": 317111,
  "title": "Week 11 Lecture and Lab Challenge 4",
  "url": "https://ukmfolio.ukm.my/mod/forum/discuss.php?d=317111",
  "post_count": 1,
  "posts": [
    {
      "post_id": 998877,
      "subject": "Week 11 Lecture and Lab Challenge 4",
      "author": "DR. WAN FARIZA BINTI PAIZI @ FAUZI",
      "timecreated": 1780000026,
      "created_local": "2026-06-04T10:00:26+08:00",
      "timemodified": 1780000026,
      "modified_local": "2026-06-04T10:00:26+08:00",
      "message_text": "Dear Students, ...",
      "message_html": "<p>Dear Students, ...</p>",
      "images": [],
      "parent_id": 0
    }
  ]
}
```

### 6. `list_documents`

List a course's document-type modules.

| Parameter | Type | Default | Description |
|------|------|------|------|
| `course` | string? | all | Course filter (recommended; iterating all courses is slow) |

```jsonc
[
  {
    "cmid": 275824,
    "name": "Course Proforma",
    "type": "resource",
    "url": "https://ukmfolio.ukm.my/mod/resource/view.php?id=275824",
    "section": "Course Information",
    "section_number": 1,
    "visible": true,
    "course_id": 15292,
    "course_shortname": "TTTN2423",
    "course_name": "Keperluan Pensuisan, ..."
  }
]
```

`type` values: `resource` (single file), `folder` (folder / multiple files), `url` (external link), `page` (in-site web page), `book` (multi-chapter).

### 7. `read_document`

Download a document and extract its text.

| Parameter | Type | Default | Description |
|------|------|------|------|
| `cmid` | int | — | From `list_documents`' `cmid` |
| `type` | string | `"resource"` | From `list_documents`' `type` |
| `extract` | bool | `true` | Whether to download and extract text; `false` returns file links only |
| `max_chars` | int | `50000` | Truncation length for each extracted text block |

```jsonc
{
  "cmid": 275824,
  "type": "resource",
  "external_url": null,
  "files": [
    {
      "filename": "TTTN2423 ... (CCNA2).pdf",
      "file_url": "https://ukmfolio.ukm.my/pluginfile.php/2430601/mod_resource/content/3/....pdf",
      "content_type": "application/pdf",
      "size_bytes": 207508,
      "text": "Proforma Kursus  1) Kod Kursus : TTTN2423 ...",
      "text_truncated": true
    }
  ]
}
```

Return differences by `type`:

- `url` module: returns `external_url` (the external link); nothing is downloaded.
- `page` / `book` module: returns `page_text` (the in-site page body).
- Unrecognized binary types: downloaded but `text` is `null`, with a `note` explaining there is no matching text extractor.
- Image files (PNG/JPG/…) are **not** downloaded here: the entry gets `is_image: true` and a `note` — pass its `file_url` to `view_image`.
- `page` / `book` bodies that embed images also return an `images` list.

### 8. `list_modules`

List **every kind** of module in a course (not just documents) — used to discover an activity's `cmid` or browse the course structure.

| Parameter | Type | Default | Description |
|------|------|------|------|
| `course` | string? | all | Course filter |

Returns each module's `cmid`, `name`, `type`, `url`, `section`, and `course_id`.

### 9. `list_images`

List every image teachers have posted, each with an `image_url` to pass to `view_image`.

| Parameter | Type | Default | Description |
|------|------|------|------|
| `course` | string? | all | Course filter (recommended — scanning is one request per discussion and per module) |
| `include_forums` | bool | `true` | Scan forum posts (embedded images + attachments, every post in each thread) |
| `include_course_content` | bool | `true` | Scan section summaries, labels / activity descriptions, page/book bodies, and image files in resource/folder modules |

```jsonc
[
  {
    "image_url": "https://ukmfoliov2.ukm.my/pluginfile.php/14980/mod_forum/post/118/WhatsApp%20Image%202026-09-25%20at%204.18.18%20PM.jpeg",
    "filename": "WhatsApp Image 2026-09-25 at 4.18.18 PM.jpeg",
    "alt": "TTTK2233",
    "source": "forum_post",
    "source_title": "2026/2027: Lecture whatsApp group",
    "source_url": "https://ukmfoliov2.ukm.my/mod/forum/discuss.php?d=116",
    "discussion_id": 116,
    "post_id": 118,
    "posted_at_local": "2026-09-25T16:23:37+08:00",
    "course_id": 3476,
    "course_shortname": "TTTK2233",
    "course_name": "TTTK2233 CYBER SECURITY"
  }
]
```

`source` is one of `forum_post`, `section`, `label`, `page`, `book`, `resource`, `folder`, or another activity type (e.g. `assign`) whose description is shown on the course page. Non-forum items carry `cmid` and `section` instead of `discussion_id`/`post_id`. Theme icons, the site logo and sidebar-block images are filtered out.

### 10. `view_image`

Fetch an image and return it as MCP image content, so the AI can actually read it (text on a poster, a QR code, a timetable …).

| Parameter | Type | Default | Description |
|------|------|------|------|
| `image_url` | string | — | An `image_url` from any tool above, or a `file_url` that `read_document` marked `is_image` |
| `max_edge` | int | `1568` | Downscale so the longer side is at most this many pixels; raise only if fine print is unreadable |

Returns two content blocks: a JSON text block with metadata, then the image.

```jsonc
{"image_url": "https://ukmfoliov2.ukm.my/pluginfile.php/14955/mod_forum/post/287/Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg",
 "filename": "Gemini_Generated_Image_lfv2fwlfv2fwlfv2.jpg",
 "original_format": "JPEG", "original_size": [1536, 2752], "original_bytes": 3504647,
 "size": [875, 1568], "bytes": 361833, "resized": true}
```

- UKM Folio images are fetched with the logged-in session (expired sessions re-login automatically).
- Externally hosted images are fetched **without** cookies, and loopback/private addresses are refused.
- JPEG/PNG/GIF/WEBP within limits pass through unchanged; larger images are downscaled, and other formats (BMP, TIFF …) are converted to JPEG (or PNG when transparent). SVG is not supported.

### 11. `whoami`

Diagnostic tool, no parameters.

```jsonc
{
  "base_url": "https://ukmfoliov2.ukm.my",
  "sesskey": "LvH2dF34SK",
  "timezone": "Asia/Kuala_Lumpur",
  "course_count": 6
}
```

---

## Typical Use Cases

Once a client is connected, you can simply ask in natural language and the AI will call the tools above for you. For example:

- **"What assignments do I have due in the next week?"** → `list_deadlines(days_ahead=7, include_past=false)`
- **"Which TTTN2423 assignments haven't I submitted yet?"** → `get_submission_status(course="TTTN2423")` → filter `submitted == false`
- **"Have any of my submissions been graded?"** → `get_submission_status()` → filter `graded == true`
- **"Any recent announcements for TTTN2423?"** → `list_announcements(course="TTTN2423", limit=5)`
- **"Read TTTN2423's Course Proforma and summarize the grade breakdown."** → `list_documents(course="TTTN2423")` to find the cmid → `read_document(cmid, "resource")` → summarize from the extracted text
- **"Which course's slides got updated this week, and what do they cover?"** → `list_documents` + `read_document` (folder/resource)
- **"Give me the full content of this announcement and any follow-up replies."** → `get_discussion(discussion_id)`
- **"What did the TTTC3213 lecturer's welcome poster say?"** → `list_announcements(course="TTTC3213")` → the item's `images[0].image_url` → `view_image(image_url)`
- **"Did any teacher post a WhatsApp/Telegram group QR code?"** → `list_images()` → `view_image` on the likely candidates

---

## Deploying to a Server (long-running)

Example using HTTP mode + systemd to keep it running in the background.

**1) Pass credentials via environment variables to avoid a plaintext config file:**

Create `/etc/ukmfolio-mcp.env` (permissions `600`):

```
UKMFOLIO_USERNAME=a207421
UKMFOLIO_PASSWORD=your_password
```

**2) systemd unit** at `/etc/systemd/system/ukmfolio-mcp.service`:

```ini
[Unit]
Description=UKMFolio MCP Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=alanwine
WorkingDirectory=/home/alanwine/Documents/CodeProjects/Python/UKMFolioMCP
EnvironmentFile=/etc/ukmfolio-mcp.env
ExecStart=/home/alanwine/PyVenv/bin/python -m ukmfolio_mcp --http-server --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**3) Enable and start:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ukmfolio-mcp
sudo systemctl status ukmfolio-mcp
```

> When exposing it publicly, put it behind a reverse proxy (Nginx/Caddy) with TLS and access control — the service itself does no authentication, so anyone who can reach it can read your Folio.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|------|----------------|
| `--check` reports login failure / `incorrect username or password` | Wrong matric number or password in `config.json`; or env vars overriding with wrong values |
| `No config.json found` | Specify an absolute path with `--config`, or set `UKMFOLIO_CONFIG` |
| Tools don't appear in the client | Check that `command` is the venv's python; use an absolute `--config` path; inspect the client's MCP logs |
| Intermittent failures while fetching data | On Moodle session expiry it auto-relogins once and retries; for transient network issues, just retry |
| `read_document` returns empty text | Likely a scanned image PDF (no text layer; OCR is not done), or an unsupported file type |
| Iterating "all courses" is slow | Documents/announcements are fetched per course, one request at a time — pass `course` to narrow the scope |
| `File exceeds 25 MB cap` | A single file is over the 25 MB cap; download it yourself using the returned `file_url` |
| `view_image`: `not a decodable image` | The URL isn't a raster image Pillow can read (e.g. SVG), or it points to a page rather than a file |
| `Step 1 failed: could not reach the SSO IdP` | The site's login entry changed; check that `base_url` is `https://ukmfoliov2.ukm.my` |

---

## Project Structure & How It Works

```
UKMFolioMCP/
├── ukmfolio_mcp/
│   ├── __main__.py     python -m ukmfolio_mcp entry point
│   ├── config.py       Read config.json + env-var overrides
│   ├── auth.py         SAML 2.0 SSO login → (session, sesskey)
│   ├── moodle.py       Moodle AJAX calls + HTML scraping (courses/deadlines/submission status/forums/documents)
│   ├── documents.py    cmid → file links → download → text extraction
│   ├── images.py       Find teacher-posted images in HTML/file lists; download + downscale for view_image
│   ├── client.py       UKMFolioClient: session caching, auto-relogin, AI-friendly shaping
│   └── server.py       FastMCP tool definitions + CLI (--stdio / --http-server)
├── tests/              Offline pytest suite (no network)
├── config.example.json Config template
├── config.json         Real credentials (git-ignored)
├── requirements.txt
├── pyproject.toml      Installable as the ukmfolio-mcp command
├── README.md           English (primary)
└── README.zh-CN.md     中文 (secondary)
```

Key technical points:

- **Authentication**: UKM Folio uses SAML SSO (a SimpleSAMLphp IdP), **not** Moodle Web Service tokens. On UKMFolio v2, `/login/index.php` shows a local login form, so SAML is started from `/login/?saml=on` instead. After login yields a `session` + `sesskey`, they are cached and reused; when Moodle reports a session-expiry error, the client re-logins and retries automatically.
- **Deadlines**: calls `core_calendar_get_action_events_by_courses` and deduplicates the multiple calendar events of one activity, keeping only the single most authoritative one per activity.
- **Submission status**: the `mod_assign_*` web-service functions are disabled here, so each assignment's view page (`/mod/assign/view.php?id=<cmid>`) is scraped for its "Submission status" summary table. Assignments are enumerated from `core_courseformat_get_state`, and the raw status strings are normalized into `submitted` / `graded` / `is_overdue` booleans.
- **Announcements/forums**: the site disables list-level forum APIs, so it first scrapes the course/forum pages to discover discussions, then uses `mod_forum_get_discussion_posts` to pull the root post content.
- **Documents**: uses `core_courseformat_get_state` to enumerate modules and infers type from the URL. A `resource` 303-redirects to the real file on `pluginfile.php`; a `folder` page has one link per file. Text extraction: `pypdf` (PDF), `python-docx` (DOCX), `python-pptx` (PPTX), `openpyxl` (XLSX), plus plain text and HTML. There is a 25 MB per-file cap, and extracted text is truncated by `max_chars` (default 50,000 characters).
- **Images**: forum images come from each post's `message` HTML plus its `attachments` / `messageinlinefiles`. The course page lazy-loads its sections, so its HTML is incomplete; labels and activity cards are rendered one by one via `core_course_get_module`, section summaries are read from `course/section.php` (only for sections with `hassummary`), and page/book/resource/folder modules go through document resolution. `view_image` returns MCP `ImageContent` with no structured output, so the base64 is not duplicated.

---

## Security & Limitations

- **Read-only**: never submits assignments, posts to forums, or changes grades.
- The SSO certificate (`CN=sso.ukm.my`) is self-signed and expired but still in use; the login flow tolerates this.
- No OCR — scanned image PDFs yield no extractable text. (Standalone images can be read by the AI through `view_image`; images inside PDFs/PPTX are not extracted.)
- The `book` module currently only fetches the first page's HTML.
- The service has no built-in authentication; when exposing HTTP mode publicly, always add a reverse proxy + TLS + access control.
- Keep credentials in environment variables or protect `config.json` (git-ignored by default).

---

📖 中文文档见 [README.zh-CN.md](./README.zh-CN.md)。
