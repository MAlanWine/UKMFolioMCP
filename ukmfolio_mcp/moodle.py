"""Moodle data access for UKMFolio.

Adapted from UKMFolioPuller ``moodle.py`` and extended with document
enumeration. UKMFolio disables most list-level web-service functions on its
AJAX endpoint, so this module mixes AJAX calls with targeted HTML scraping —
exactly the approach proven to work in the Puller.

Working AJAX methods (via ``/lib/ajax/service.php`` with session cookie + sesskey):
  - core_course_get_enrolled_courses_by_timeline_classification
  - core_calendar_get_action_events_by_courses
  - core_courseformat_get_state              (course module list, incl. documents)
  - mod_forum_get_discussion_posts

Disabled on this install (must be HTML-scraped instead):
  - core_course_get_contents, mod_assign_get_assignments,
    mod_forum_get_forums_by_courses, mod_forum_get_forum_discussions_paginated
"""

from __future__ import annotations

import html as html_mod
import json
import re

import requests
from bs4 import BeautifulSoup

# --- URL patterns -----------------------------------------------------------

_FORUM_VIEW_RE = re.compile(r"/mod/forum/view\.php\?id=(\d+)")
_DISCUSS_LINK_RE = re.compile(r"/mod/forum/discuss\.php\?d=(\d+)")
_MODULE_TYPE_RE = re.compile(r"/mod/([a-z0-9]+)/view\.php")
_TAG_RE = re.compile(r"<[^>]+>")

# Module types that represent "documents" / learning materials a student reads.
DOCUMENT_MODULE_TYPES = {"resource", "folder", "url", "page", "book"}

# Moodle session-expiry error codes surfaced by the AJAX endpoint.
_SESSION_ERROR_CODES = {
    "servicerequireslogin",
    "requireloginerror",
    "invalidsesskey",
    "sessionerror",
    "loggedinnot",
    "notloggedin",
}


# --- exceptions -------------------------------------------------------------

class MoodleError(RuntimeError):
    """A Moodle AJAX call returned an application-level exception."""

    def __init__(self, errorcode: str, message: str):
        self.errorcode = errorcode
        self.message = message
        super().__init__(f"Moodle API error [{errorcode}]: {message}")


class SessionExpired(MoodleError):
    """The Moodle session/sesskey is no longer valid — caller should re-login."""


# --- helpers ----------------------------------------------------------------

def _strip_tags(s: str) -> str:
    return html_mod.unescape(_TAG_RE.sub("", s or "")).strip()


def _clean(s: str) -> str:
    return html_mod.unescape((s or "").strip())


def _ajax_call(session: requests.Session, sesskey: str, base_url: str,
               method: str, args: dict):
    """Call the Moodle AJAX service endpoint and return the ``data`` payload."""
    resp = session.post(
        f"{base_url}/lib/ajax/service.php",
        params={"sesskey": sesskey, "info": method},
        json=[{"index": 0, "methodname": method, "args": args}],
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list) and data and data[0].get("error"):
        exc = data[0]["exception"]
        code = exc.get("errorcode", "")
        message = exc.get("message", "")
        if code in _SESSION_ERROR_CODES:
            raise SessionExpired(code, message)
        raise MoodleError(code, message)
    return data[0]["data"]


# --- courses ----------------------------------------------------------------

def get_enrolled_courses(session, sesskey, base_url) -> list[dict]:
    """Fetch enrolled courses as normalized dicts."""
    data = _ajax_call(
        session, sesskey, base_url,
        "core_course_get_enrolled_courses_by_timeline_classification",
        {"classification": "all", "limit": 0, "offset": 0, "sort": "fullname"},
    )
    courses = []
    for c in data["courses"]:
        courses.append({
            "course_id": c["id"],
            "course_name": c["fullname"],
            "course_shortname": c.get("shortname", ""),
            "course_category": c.get("coursecategory", ""),
            "course_url": c.get("viewurl", f"{base_url}/course/view.php?id={c['id']}"),
            "progress": c.get("progress"),
        })
    return courses


# --- assignment / quiz deadlines (calendar action events) -------------------

# One activity may surface several calendar events (due/close/gradingdue/
# expectcompletionon); keep only the most authoritative one per activity.
_EVENTTYPE_PRIORITY = {"due": 0, "close": 0, "gradingdue": 5, "expectcompletionon": 10}


def _event_rank(event: dict) -> int:
    return _EVENTTYPE_PRIORITY.get(event.get("eventtype", ""), 3)


def get_action_events(session, sesskey, base_url, course_ids) -> list[dict]:
    """Fetch assignment/quiz deadline events for the given courses.

    Deduplicates per-activity so each (modulename, instance) yields one item.
    """
    data = _ajax_call(
        session, sesskey, base_url,
        "core_calendar_get_action_events_by_courses",
        {"courseids": list(course_ids), "timesortfrom": 0},
    )

    chosen: dict[tuple, dict] = {}
    loose: list[dict] = []
    for group in data["groupedbycourse"]:
        for event in group["events"]:
            instance = event.get("instance")
            if instance is None:
                loose.append(event)
                continue
            key = (event.get("modulename"), instance)
            prev = chosen.get(key)
            if prev is None or _event_rank(event) < _event_rank(prev):
                chosen[key] = event

    items = []
    for event in list(chosen.values()) + loose:
        items.append({
            "item_id": event["id"],
            "item_type": event.get("modulename", "unknown"),
            "item_title": _clean(event.get("activityname") or event.get("name") or ""),
            "deadline": event.get("timestart"),
            "item_url": event.get("url", ""),
            "course_id": event["course"]["id"],
        })
    return items


# --- forum discussions / announcements --------------------------------------

def get_forum_cmids(session, sesskey, base_url, course_id) -> list[int]:
    """Scrape a course page for its forum module ids (cmids)."""
    r = session.get(f"{base_url}/course/view.php",
                    params={"id": course_id}, timeout=30)
    r.raise_for_status()
    if "/login/index.php" in r.url:
        raise SessionExpired("notloggedin", "redirected to login page")
    return sorted({int(m) for m in _FORUM_VIEW_RE.findall(r.text)})


def get_discussion_ids(session, base_url, cmid) -> list[int]:
    """Scrape a forum page for its discussion ids."""
    r = session.get(f"{base_url}/mod/forum/view.php",
                    params={"id": cmid}, timeout=30)
    r.raise_for_status()
    if "/login/index.php" in r.url:
        raise SessionExpired("notloggedin", "redirected to login page")
    return sorted({int(m) for m in _DISCUSS_LINK_RE.findall(r.text)})


def get_discussion_posts(session, sesskey, base_url, discussion_id) -> list[dict]:
    """Fetch all posts of a single discussion (full thread), oldest first."""
    data = _ajax_call(session, sesskey, base_url,
                      "mod_forum_get_discussion_posts",
                      {"discussionid": discussion_id})
    posts = data.get("posts") or []
    out = []
    for p in posts:
        author = p.get("author") or {}
        out.append({
            "post_id": p.get("id"),
            "subject": _clean(p.get("subject")),
            "author": author.get("fullname", ""),
            "timecreated": p.get("timecreated"),
            "timemodified": p.get("timemodified"),
            "message_html": p.get("message") or "",
            "message_text": _strip_tags(p.get("message") or ""),
            "parent_id": p.get("parentid"),
        })
    out.sort(key=lambda p: p.get("post_id") or 0)
    return out


def get_forum_discussions(session, sesskey, base_url, course_ids,
                          with_body: bool = True) -> list[dict]:
    """Enumerate visible forum discussions across the given courses.

    Three-step hybrid (list-level forum AJAX is disabled on UKMFolio):
      1. course page -> forum cmids (dedup across courses for site-wide forums)
      2. forum page  -> discussion ids
      3. AJAX root post per discussion -> title/posted-at/body
    """
    items: list[dict] = []
    seen_forum_cmids: set[int] = set()

    for course_id in course_ids:
        try:
            forum_cmids = get_forum_cmids(session, sesskey, base_url, course_id)
        except SessionExpired:
            raise
        except Exception:
            continue

        for cmid in forum_cmids:
            if cmid in seen_forum_cmids:
                continue
            seen_forum_cmids.add(cmid)

            try:
                discussion_ids = get_discussion_ids(session, base_url, cmid)
            except SessionExpired:
                raise
            except Exception:
                continue

            for did in discussion_ids:
                try:
                    posts = get_discussion_posts(session, sesskey, base_url, did)
                except SessionExpired:
                    raise
                except Exception:
                    continue
                if not posts:
                    continue
                root = posts[0]  # already sorted oldest-first by post id
                item = {
                    "item_id": did,
                    "item_type": "forum",
                    "item_title": root["subject"] or f"(discussion {did})",
                    "posted_at": root.get("timecreated"),
                    "author": root.get("author", ""),
                    "item_url": f"{base_url}/mod/forum/discuss.php?d={did}",
                    "course_id": course_id,
                    "forum_cmid": cmid,
                    "reply_count": max(len(posts) - 1, 0),
                }
                if with_body:
                    item["item_body"] = root["message_text"]
                items.append(item)

    return items


# --- documents (course modules) ---------------------------------------------

def get_course_modules(session, sesskey, base_url, course_id) -> dict:
    """Return the raw ``core_courseformat_get_state`` payload (cm + section)."""
    data = _ajax_call(session, sesskey, base_url,
                      "core_courseformat_get_state", {"courseid": course_id})
    if isinstance(data, str):
        data = json.loads(data)
    return data


def _module_type(url: str) -> str | None:
    m = _MODULE_TYPE_RE.search(url or "")
    return m.group(1) if m else None


def get_documents(session, sesskey, base_url, course_id) -> list[dict]:
    """List document-type modules (resources/folders/urls/pages/books).

    Section names are joined in so the AI can see where a document lives.
    """
    state = get_course_modules(session, sesskey, base_url, course_id)

    section_names: dict = {}
    for sec in state.get("section", []):
        section_names[str(sec.get("id"))] = sec.get("title") or sec.get("name") or ""

    docs = []
    for cm in state.get("cm", []):
        url = cm.get("url") or ""
        mtype = _module_type(url)
        if mtype not in DOCUMENT_MODULE_TYPES:
            continue
        if not cm.get("uservisible", True):
            continue
        docs.append({
            "cmid": int(cm["id"]),
            "name": _clean(cm.get("name", "")),
            "type": mtype,
            "url": url,
            "section": section_names.get(str(cm.get("sectionid")), ""),
            "section_number": cm.get("sectionnumber"),
            "visible": bool(cm.get("visible", True)),
        })
    return docs


def list_all_modules(session, sesskey, base_url, course_id) -> list[dict]:
    """List every visible course module with its detected type (any kind)."""
    state = get_course_modules(session, sesskey, base_url, course_id)
    section_names: dict = {}
    for sec in state.get("section", []):
        section_names[str(sec.get("id"))] = sec.get("title") or sec.get("name") or ""
    mods = []
    for cm in state.get("cm", []):
        url = cm.get("url") or ""
        mods.append({
            "cmid": int(cm["id"]),
            "name": _clean(cm.get("name", "")),
            "type": _module_type(url) or "label",
            "url": url,
            "section": section_names.get(str(cm.get("sectionid")), ""),
            "section_number": cm.get("sectionnumber"),
        })
    return mods


# --- assignment submission status -------------------------------------------
# The assignment web-service functions (mod_assign_get_submission_status, ...)
# are disabled on this install, so submission status is scraped from the
# assignment view page's "Submission status" summary table — the same
# AJAX-disabled / HTML-scrape pattern used for forums.

ASSIGN_MODULE_TYPE = "assign"


def get_assignment_modules(session, sesskey, base_url, course_id) -> list[dict]:
    """List assignment-type modules in a course (cmid, name, section)."""
    state = get_course_modules(session, sesskey, base_url, course_id)
    section_names: dict = {}
    for sec in state.get("section", []):
        section_names[str(sec.get("id"))] = sec.get("title") or sec.get("name") or ""
    out = []
    for cm in state.get("cm", []):
        if _module_type(cm.get("url") or "") != ASSIGN_MODULE_TYPE:
            continue
        if not cm.get("uservisible", True):
            continue
        out.append({
            "cmid": int(cm["id"]),
            "name": _clean(cm.get("name", "")),
            "url": cm.get("url") or "",
            "section": section_names.get(str(cm.get("sectionid")), ""),
        })
    return out


def _norm_submitted(raw: str | None) -> bool | None:
    """True = submitted for grading, False = not submitted / draft, None = ?."""
    if not raw:
        return None
    low = raw.lower()
    if "submitted for grading" in low:
        return True
    if ("no submissions have been made" in low
            or "nothing has been submitted" in low
            or "no attempt" in low):
        return False
    if "draft" in low:  # "Draft (not submitted)"
        return False
    return None


def _norm_graded(raw: str | None) -> bool | None:
    """True = graded/released, False = not graded, None = unknown."""
    if not raw:
        return None
    low = raw.lower()
    if "not graded" in low:  # check before the bare "graded" substring
        return False
    if "graded" in low or "released" in low:
        return True
    return None


def _parse_assignment_status(html: str, base_url: str, cmid: int) -> dict:
    """Parse the submission-status summary table on an assign view page."""
    soup = BeautifulSoup(html, "lxml")
    table = (soup.find("table", class_=re.compile("submissionsummarytable"))
             or soup.find("table", class_=re.compile("submissionstatustable")))
    rows: dict[str, str] = {}
    if table is None:  # fall back: a generaltable whose first row is the status
        for t in soup.find_all("table"):
            first = t.find("tr")
            if first and "Submission status" in first.get_text():
                table = t
                break
    if table is not None:
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"])
            if len(cells) >= 2:
                rows[cells[0].get_text(" ", strip=True)] = \
                    cells[1].get_text(" ", strip=True)

    name = ""
    heading = soup.find("h2")
    if heading:
        name = _clean(heading.get_text(" ", strip=True))

    sub_raw = rows.get("Submission status")
    grad_raw = rows.get("Grading status")
    remaining = rows.get("Time remaining")
    last_mod = rows.get("Last modified")
    grade = rows.get("Grade")

    is_overdue = None
    if remaining:
        is_overdue = "overdue" in remaining.lower()

    return {
        "cmid": int(cmid),
        "name": name,
        "item_type": "assign",
        "item_url": f"{base_url}/mod/assign/view.php?id={cmid}",
        "submission_status": sub_raw,
        "submitted": _norm_submitted(sub_raw),
        "grading_status": grad_raw,
        "graded": _norm_graded(grad_raw),
        "time_remaining": remaining,
        "is_overdue": is_overdue,
        "last_modified": None if last_mod in (None, "-", "") else last_mod,
        "grade": grade,
        "status_found": table is not None,
    }


def get_assignment_status(session, base_url, cmid) -> dict:
    """Scrape /mod/assign/view.php?id=<cmid> for its submission status."""
    r = session.get(f"{base_url}/mod/assign/view.php",
                    params={"id": cmid}, timeout=30)
    r.raise_for_status()
    if "/login/index.php" in r.url:
        raise SessionExpired("notloggedin", "redirected to login page")
    return _parse_assignment_status(r.text, base_url, int(cmid))
