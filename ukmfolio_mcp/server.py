"""UKMFolio MCP server — tool definitions and CLI entry point.

Exposes UKM Folio content to AI agents over MCP. Tools are plain functions:
the MCP SDK (v2) runs sync handlers on a worker thread, so the blocking
``requests``-based client work never stalls the event loop (important for the
HTTP transport).

Run:
    python -m ukmfolio_mcp --stdio                 # local (default)
    python -m ukmfolio_mcp --http-server           # remote, streamable HTTP
    python -m ukmfolio_mcp --check                 # smoke-test login + counts
"""

from __future__ import annotations

import argparse
import functools
import json
import sys

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import __version__
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
reports due dates).

Teachers often post the actual content of an announcement as an image (a
poster, schedule, or QR code). Wherever an image appears, results include an
`images` list (each with an `image_url`) and post text contains an inline
marker `[image: <alt> | <image_url>]`; files that are images carry
`is_image: true`. When an image may hold relevant information, call
`view_image(image_url)` to see it. `list_images` lists every image in a
course.\
"""

mcp = MCPServer("ukmfolio", instructions=INSTRUCTIONS, version=__version__)

STREAMABLE_HTTP_PATH = "/mcp"

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


def tool(**kwargs):
    """``@mcp.tool`` that reports failures to the model.

    MCP SDK v2 hides the text of unexpected exceptions from the client (it
    sends only "Error executing tool X"). Our failures — login rejected, an
    HTTP 404, an undecodable image — are exactly what the AI needs to recover,
    so surface them as ``ToolError``.
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kw):
            try:
                return fn(*args, **kw)
            except ToolError:
                raise
            except Exception as e:
                raise ToolError(f"{type(e).__name__}: {e}") from e
        return mcp.tool(**kwargs)(wrapper)
    return decorator


# --- tools ------------------------------------------------------------------

@tool()
def list_courses() -> list[dict]:
    """List the student's enrolled courses.

    Returns each course's id, full name, shortname (often the course code),
    category, progress percent, and a direct URL.
    """
    return get_client().get_courses()


@tool()
def list_deadlines(course: str | None = None,
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
    return get_client().get_deadlines(course=course, days_ahead=days_ahead,
                                      include_past=include_past)


@tool()
def get_submission_status(course: str | None = None,
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
    return get_client().get_submission_status(course=course, cmid=cmid)


@tool()
def list_announcements(course: str | None = None,
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

    `images` lists every image posted anywhere in the thread (image_url,
    filename, alt, post_id); item_body marks where each sits as
    `[image: <alt> | <image_url>]`. Pass an image_url to view_image to see it —
    announcements are often just a poster with no text.
    """
    return get_client().get_announcements(course=course, limit=limit,
                                          with_body=with_body)


@tool()
def get_discussion(discussion_id: int) -> dict:
    """Fetch the full thread of a forum discussion by its id.

    Returns the discussion title, url, post_count, and every post (oldest
    first) with author, subject, created/modified timestamps, and both the
    HTML and plain-text message bodies. Each post's `images` lists embedded
    and attached images (pass image_url to view_image); message_text marks
    them inline as `[image: <alt> | <image_url>]`.
    """
    return get_client().get_discussion(discussion_id)


@tool()
def list_documents(course: str | None = None) -> list[dict]:
    """List downloadable documents / learning materials in a course.

    Covers Moodle resource (single file), folder (file bundle), url (external
    link), page and book modules. Each item includes cmid, name, type, url,
    section, and course info. Pass a document's cmid + type to read_document
    to extract its text.

    Args:
        course: Optional course filter (id, shortname, or name substring).
            Omitting it scans every enrolled course (slower).
    """
    return get_client().list_documents(course=course)


@tool()
def read_document(cmid: int, type: str = "resource",
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
    Image files are not downloaded here: they carry is_image=true — pass their
    file_url to view_image. Page/book bodies list embedded images in `images`.
    """
    return get_client().get_document_content(cmid=cmid, module_type=type,
                                             extract=extract,
                                             max_chars=max_chars)


@tool()
def list_modules(course: str | None = None) -> list[dict]:
    """List ALL course modules of any kind (assignments, forums, resources,
    quizzes, labels, ...) with their detected type and cmid.

    Broader than list_documents — useful for discovering an activity's cmid or
    seeing a course's full structure.

    Args:
        course: Optional course filter (id, shortname, or name substring).
    """
    return get_client().list_modules(course=course)


@tool()
def list_images(course: str | None = None,
                include_forums: bool = True,
                include_course_content: bool = True) -> list[dict]:
    """List images teachers have posted, with an image_url for each.

    Scans forum discussions (images embedded in or attached to any post) and
    course content: section summaries, labels / activity descriptions,
    page/book bodies, and image files in resource/folder modules.

    Args:
        course: Optional course filter (id, shortname, or name substring).
            Omitting it scans every enrolled course (slow — one request per
            discussion and per module).
        include_forums: Scan forum posts (default True).
        include_course_content: Scan non-forum course content (default True).

    Each item includes image_url (pass it to view_image), filename, alt text,
    source (forum_post/section/label/page/book/resource/folder/assign/...),
    source_title, source_url, cmid or discussion_id + post_id,
    posted_at_local for forum images, and course info.
    """
    return get_client().list_images(course=course,
                                    include_forums=include_forums,
                                    include_course_content=include_course_content)


@tool(structured_output=False)
def view_image(image_url: str, max_edge: int = 1568) -> list:
    """Fetch an image and return it so you can see its content.

    Use this for any image_url from list_images / list_announcements /
    get_discussion (`images`), or a file_url that read_document marked
    is_image. UKMFolio images are fetched with the student's login session.

    Args:
        image_url: The image's URL exactly as returned by another tool.
        max_edge: Downscale so the longer side is at most this many pixels
            (default 1568). Raise it only if fine print is unreadable.

    Returns a JSON text block (original/returned size and format) followed by
    the image itself.
    """
    data, fmt, info = get_client().view_image(image_url, max_edge=max_edge)
    return [json.dumps(info, ensure_ascii=False), Image(data=data, format=fmt)]


@tool()
def whoami() -> dict:
    """Diagnostic: confirm the session is authenticated and report base_url,
    timezone and the number of enrolled courses."""
    return get_client().whoami()


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
              f"{STREAMABLE_HTTP_PATH}", file=sys.stderr)
        mcp.run(transport="streamable-http", host=config.host,
                port=config.port, streamable_http_path=STREAMABLE_HTTP_PATH)
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
