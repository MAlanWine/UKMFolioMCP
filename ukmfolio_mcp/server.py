"""UKMFolio MCP server — tool definitions and CLI entry point.

Exposes UKM Folio content to AI agents over MCP. Tools are async and offload
the blocking ``requests``-based client work to a worker thread so the event
loop stays responsive (important for the HTTP transport).

Run:
    python -m ukmfolio_mcp --stdio                 # local (default)
    python -m ukmfolio_mcp --http-server           # remote, streamable HTTP
    python -m ukmfolio_mcp --check                 # smoke-test login + counts
"""

from __future__ import annotations

import argparse
import functools
import sys

import anyio
from mcp.server.fastmcp import FastMCP

from .client import UKMFolioClient
from .config import Config, load_config

INSTRUCTIONS = """\
Access to UKM Folio (Universiti Kebangsaan Malaysia's Moodle LMS) for the
signed-in student. Use these tools to answer questions about courses,
assignment/quiz deadlines, forum announcements, and course documents.

Most tools accept an optional `course` filter — a course id, shortname
(e.g. "TTTN2423"), or any substring of the course name. Omit it to span all
enrolled courses. Timestamps are returned both as unix seconds and as
ISO-8601 strings in the configured timezone (default Asia/Kuala_Lumpur).

Typical flow for reading a document: call `list_documents` to get a `cmid`
and `type`, then call `read_document(cmid, type)` to download and extract its
text.

To check whether assignments have been submitted/graded, use
`get_submission_status` (this is separate from `list_deadlines`, which only
reports due dates).\
"""

mcp = FastMCP("ukmfolio", instructions=INSTRUCTIONS)

_config: Config | None = None
_client: UKMFolioClient | None = None


def get_client() -> UKMFolioClient:
    global _client
    if _client is None:
        if _config is None:
            raise RuntimeError("Server not configured; call configure() first")
        _client = UKMFolioClient(_config)
    return _client


def configure(config: Config) -> None:
    global _config, _client
    _config = config
    _client = None
    mcp.settings.host = config.host
    mcp.settings.port = config.port


async def _run(fn, *args, **kwargs):
    """Offload a blocking client call to a worker thread."""
    return await anyio.to_thread.run_sync(
        functools.partial(fn, *args, **kwargs))


# --- tools ------------------------------------------------------------------

@mcp.tool()
async def list_courses() -> list[dict]:
    """List the student's enrolled courses.

    Returns each course's id, full name, shortname (often the course code),
    category, progress percent, and a direct URL.
    """
    return await _run(get_client().get_courses)


@mcp.tool()
async def list_deadlines(course: str | None = None,
                         days_ahead: int | None = None,
                         include_past: bool = True) -> list[dict]:
    """List assignment and quiz deadlines (Moodle calendar action events).

    Args:
        course: Optional course filter (id, shortname, or name substring).
        days_ahead: If set, only return items due within this many days.
        include_past: Include items whose deadline has already passed
            (default True). Set False for "upcoming only".

    Each item includes item_title, item_type (assign/quiz), deadline (unix),
    deadline_local (ISO), days_left, item_url, and course info. Sorted by
    deadline ascending.
    """
    return await _run(get_client().get_deadlines,
                      course=course, days_ahead=days_ahead,
                      include_past=include_past)


@mcp.tool()
async def get_submission_status(course: str | None = None,
                                cmid: int | None = None) -> list[dict]:
    """Check assignment submission status: submitted? graded? overdue?

    UKM Folio disables the assignment web-service APIs, so this scrapes each
    assignment's "Submission status" page (one HTTP request per assignment).

    Args:
        course: Optional course filter (id, shortname, or name substring).
            Omit to scan every enrolled course (slower).
        cmid: Check a single assignment by its course-module id (the id in an
            assignment's item_url from list_deadlines, or from list_modules).
            Takes precedence over `course`.

    Each item includes name, submission_status (raw) + submitted (bool),
    grading_status (raw) + graded (bool), time_remaining + is_overdue, grade,
    last_modified, item_url, and course info. submitted/graded/is_overdue are
    null when the page format is unrecognized (status_found=false).
    """
    return await _run(get_client().get_submission_status,
                      course=course, cmid=cmid)


@mcp.tool()
async def list_announcements(course: str | None = None,
                             limit: int = 20,
                             with_body: bool = True) -> list[dict]:
    """List forum discussions / announcements (notifications) across courses.

    Args:
        course: Optional course filter (id, shortname, or name substring).
        limit: Max items to return, newest first (default 20).
        with_body: Include the plain-text body of each discussion's root post.

    Each item includes item_title, author, posted_at (unix), posted_at_local
    (ISO), reply_count, item_url, item_id (the discussion id — pass it to
    get_discussion for the full thread), and course info.
    """
    return await _run(get_client().get_announcements,
                      course=course, limit=limit, with_body=with_body)


@mcp.tool()
async def get_discussion(discussion_id: int) -> dict:
    """Fetch the full thread of a forum discussion by its id.

    Returns the discussion title, url, post_count, and every post (oldest
    first) with author, subject, created/modified timestamps, and both the
    HTML and plain-text message bodies.
    """
    return await _run(get_client().get_discussion, discussion_id)


@mcp.tool()
async def list_documents(course: str | None = None) -> list[dict]:
    """List downloadable documents / learning materials in a course.

    Covers Moodle resource (single file), folder (file bundle), url (external
    link), page and book modules. Each item includes cmid, name, type, url,
    section, and course info. Pass a document's cmid + type to read_document
    to extract its text.

    Args:
        course: Optional course filter (id, shortname, or name substring).
            Omitting it scans every enrolled course (slower).
    """
    return await _run(get_client().list_documents, course=course)


@mcp.tool()
async def read_document(cmid: int, type: str = "resource",
                        extract: bool = True,
                        max_chars: int = 50000) -> dict:
    """Download a document and extract its text content.

    Args:
        cmid: The course-module id from list_documents.
        type: The module type from list_documents (resource/folder/url/page/
            book). Determines how the document is resolved.
        extract: When True (default), download files and extract text from
            PDF/DOCX/PPTX/XLSX/TXT/HTML. When False, return file URLs only.
        max_chars: Truncate each extracted text block to this length.

    Returns the resolved files (filename, file_url, content_type, size_bytes,
    extracted text), plus external_url for url modules and page_text for
    page/book modules. text_truncated/page_text_truncated flag truncation.
    """
    return await _run(get_client().get_document_content,
                      cmid=cmid, module_type=type,
                      extract=extract, max_chars=max_chars)


@mcp.tool()
async def list_modules(course: str | None = None) -> list[dict]:
    """List ALL course modules of any kind (assignments, forums, resources,
    quizzes, labels, ...) with their detected type and cmid.

    Broader than list_documents — useful for discovering an activity's cmid or
    seeing a course's full structure.

    Args:
        course: Optional course filter (id, shortname, or name substring).
    """
    return await _run(get_client().list_modules, course=course)


@mcp.tool()
async def whoami() -> dict:
    """Diagnostic: confirm the session is authenticated and report base_url,
    timezone and the number of enrolled courses."""
    return await _run(get_client().whoami)


# --- CLI --------------------------------------------------------------------

def _check(client: UKMFolioClient) -> int:
    """Smoke-test: log in and print quick counts."""
    print("[*] Logging in to UKM Folio …", file=sys.stderr)
    info = client.whoami()
    print(f"[*] Authenticated. base_url={info['base_url']} "
          f"sesskey={info['sesskey']} tz={info['timezone']}", file=sys.stderr)
    courses = client.get_courses()
    print(f"[*] {len(courses)} enrolled courses:", file=sys.stderr)
    for c in courses:
        print(f"      {c['course_id']:>7}  {c['course_shortname']:<28} "
              f"{c['course_name'][:50]}", file=sys.stderr)
    deadlines = client.get_deadlines(include_past=False, days_ahead=14)
    print(f"[*] {len(deadlines)} deadlines in next 14 days.", file=sys.stderr)
    print("[*] OK", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ukmfolio-mcp",
        description="MCP server exposing UKM Folio (Moodle) to AI agents.")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--stdio", action="store_true",
                           help="Serve over stdio (default).")
    transport.add_argument("--http-server", action="store_true",
                           help="Serve over streamable HTTP (remote hosting).")
    parser.add_argument("--config", help="Path to config.json.")
    parser.add_argument("--host", help="HTTP bind host (overrides config).")
    parser.add_argument("--port", type=int, help="HTTP bind port (overrides config).")
    parser.add_argument("--check", action="store_true",
                        help="Test login + print course/deadline counts, then exit.")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except (FileNotFoundError, ValueError) as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    configure(config)

    if args.check:
        try:
            return _check(get_client())
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
            return 1

    if args.http_server:
        print(f"[*] UKMFolio MCP server on http://{config.host}:{config.port}"
              f"{mcp.settings.streamable_http_path}", file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
