"""High-level UKMFolio client used by the MCP tools.

Wraps the SAML session + sesskey, caches the login, transparently re-logs-in
when the Moodle session expires, and returns AI-friendly structured data
(ISO timestamps in the configured timezone, course names joined in, etc.).
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import documents, moodle
from .auth import login
from .config import Config
from .moodle import SessionExpired

_COURSE_CACHE_TTL = 300  # seconds


class UKMFolioClient:
    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self._login_dict = config.as_login_dict()
        self._session = None
        self._sesskey = None
        self._lock = threading.RLock()
        try:
            self._tz = ZoneInfo(config.timezone)
        except Exception:
            self._tz = timezone.utc
        self._courses: list[dict] | None = None
        self._courses_at = 0.0

    # --- session management -------------------------------------------------

    def _ensure(self):
        with self._lock:
            if self._session is None or self._sesskey is None:
                self._session, self._sesskey = login(self._login_dict)
            return self._session, self._sesskey

    def _relogin(self):
        with self._lock:
            self._session, self._sesskey = login(self._login_dict)
            self._courses = None  # course ids could change after re-auth
            return self._session, self._sesskey

    def _call(self, fn):
        """Run ``fn(session, sesskey)``; re-login once on session expiry."""
        session, sesskey = self._ensure()
        try:
            return fn(session, sesskey)
        except SessionExpired:
            session, sesskey = self._relogin()
            return fn(session, sesskey)

    # --- timestamp helpers --------------------------------------------------

    def _iso(self, ts):
        if not ts:
            return None
        return datetime.fromtimestamp(ts, tz=self._tz).isoformat()

    @staticmethod
    def _days_from_now(ts):
        if not ts:
            return None
        return round((ts - time.time()) / 86400, 1)

    # --- courses ------------------------------------------------------------

    def get_courses(self, force: bool = False) -> list[dict]:
        with self._lock:
            fresh = (
                self._courses is not None
                and (time.time() - self._courses_at) < _COURSE_CACHE_TTL
            )
            if fresh and not force:
                return self._courses
        courses = self._call(
            lambda s, k: moodle.get_enrolled_courses(s, k, self.base_url))
        with self._lock:
            self._courses = courses
            self._courses_at = time.time()
        return courses

    def _course_map(self) -> dict[int, dict]:
        return {c["course_id"]: c for c in self.get_courses()}

    def _resolve_course_ids(self, course_id=None, course=None) -> list[int]:
        """Resolve a single course filter to a list of ids.

        ``course`` may be a course id, shortname substring, or name substring.
        """
        cmap = self._course_map()
        if course_id is not None:
            return [int(course_id)]
        if course:
            needle = str(course).lower()
            matches = [
                cid for cid, c in cmap.items()
                if needle in str(c["course_shortname"]).lower()
                or needle in str(c["course_name"]).lower()
                or needle == str(cid)
            ]
            if matches:
                return matches
        return list(cmap.keys())

    def _enrich(self, item: dict, cmap: dict) -> dict:
        c = cmap.get(item.get("course_id"), {})
        item["course_shortname"] = c.get("course_shortname", "")
        item["course_name"] = c.get("course_name", "")
        return item

    # --- deadlines (assignments / quizzes) ----------------------------------

    def get_deadlines(self, course_id=None, course=None, days_ahead=None,
                      include_past=True) -> list[dict]:
        cmap = self._course_map()
        course_ids = self._resolve_course_ids(course_id, course)
        events = self._call(
            lambda s, k: moodle.get_action_events(s, k, self.base_url, course_ids))

        now = time.time()
        out = []
        for e in events:
            ts = e.get("deadline")
            if not include_past and ts and ts < now:
                continue
            if days_ahead is not None and ts and ts > now + days_ahead * 86400:
                continue
            self._enrich(e, cmap)
            e["deadline_local"] = self._iso(ts)
            e["days_left"] = self._days_from_now(ts)
            out.append(e)
        out.sort(key=lambda x: (x.get("deadline") is None, x.get("deadline") or 0))
        return out

    # --- announcements / forum discussions ----------------------------------

    def get_announcements(self, course_id=None, course=None, limit=None,
                          with_body=True) -> list[dict]:
        cmap = self._course_map()
        course_ids = self._resolve_course_ids(course_id, course)
        items = self._call(
            lambda s, k: moodle.get_forum_discussions(
                s, k, self.base_url, course_ids, with_body=with_body))
        for it in items:
            self._enrich(it, cmap)
            it["posted_at_local"] = self._iso(it.get("posted_at"))
        items.sort(key=lambda x: x.get("posted_at") or 0, reverse=True)
        if limit:
            items = items[:limit]
        return items

    def get_discussion(self, discussion_id: int) -> dict:
        posts = self._call(
            lambda s, k: moodle.get_discussion_posts(
                s, k, self.base_url, discussion_id))
        for p in posts:
            p["created_local"] = self._iso(p.get("timecreated"))
            p["modified_local"] = self._iso(p.get("timemodified"))
        title = posts[0]["subject"] if posts else ""
        return {
            "discussion_id": discussion_id,
            "title": title,
            "url": f"{self.base_url}/mod/forum/discuss.php?d={discussion_id}",
            "post_count": len(posts),
            "posts": posts,
        }

    # --- assignment submission status ---------------------------------------

    def get_submission_status(self, course_id=None, course=None,
                              cmid=None) -> list[dict]:
        """Submission status per assignment.

        With ``cmid`` set, checks just that one assignment. Otherwise
        enumerates every assignment module in the resolved course(s) and
        scrapes each one's status page.
        """
        if cmid is not None:
            st = self._call(
                lambda s, k: moodle.get_assignment_status(
                    s, self.base_url, int(cmid)))
            cmap = self._course_map()
            self._enrich(st, cmap)  # course_id is unknown here -> blanks; ok
            return [st]

        cmap = self._course_map()
        course_ids = self._resolve_course_ids(course_id, course)
        out = []
        for cid in course_ids:
            mods = self._call(
                lambda s, k, cid=cid: moodle.get_assignment_modules(
                    s, k, self.base_url, cid))
            c = cmap.get(cid, {})
            for m in mods:
                st = self._call(
                    lambda s, k, cm=m["cmid"]: moodle.get_assignment_status(
                        s, self.base_url, cm))
                st["name"] = m.get("name") or st.get("name")  # state name is authoritative
                st["section"] = m.get("section", "")
                st["course_id"] = cid
                st["course_shortname"] = c.get("course_shortname", "")
                st["course_name"] = c.get("course_name", "")
                out.append(st)
        return out

    # --- documents ----------------------------------------------------------

    def list_documents(self, course_id=None, course=None) -> list[dict]:
        cmap = self._course_map()
        course_ids = self._resolve_course_ids(course_id, course)
        out = []
        for cid in course_ids:
            docs = self._call(
                lambda s, k, cid=cid: moodle.get_documents(s, k, self.base_url, cid))
            c = cmap.get(cid, {})
            for d in docs:
                d["course_id"] = cid
                d["course_shortname"] = c.get("course_shortname", "")
                d["course_name"] = c.get("course_name", "")
            out.extend(docs)
        return out

    def list_modules(self, course_id=None, course=None) -> list[dict]:
        course_ids = self._resolve_course_ids(course_id, course)
        out = []
        for cid in course_ids:
            mods = self._call(
                lambda s, k, cid=cid: moodle.list_all_modules(
                    s, k, self.base_url, cid))
            for m in mods:
                m["course_id"] = cid
            out.extend(mods)
        return out

    def get_document_content(self, cmid: int, module_type: str = "resource",
                             extract: bool = True,
                             max_chars: int = 50000) -> dict:
        resolved = self._call(
            lambda s, k: documents.resolve_files(
                s, self.base_url, cmid, module_type))

        result = {
            "cmid": cmid,
            "type": resolved["type"],
            "external_url": resolved.get("external_url"),
            "files": [],
        }
        if resolved.get("page_text") is not None:
            text = resolved["page_text"]
            result["page_text"], result["page_text_truncated"] = _truncate(
                text, max_chars)

        for f in resolved["files"]:
            entry = {"filename": f["filename"], "file_url": f["file_url"]}
            if extract:
                try:
                    filename, ct, data = self._call(
                        lambda s, k, url=f["file_url"]:
                        documents.download_file(s, url))
                except SessionExpired:
                    raise
                except Exception as e:
                    entry["error"] = f"download failed: {type(e).__name__}: {e}"
                    result["files"].append(entry)
                    continue
                entry["filename"] = filename
                entry["content_type"] = ct
                entry["size_bytes"] = len(data)
                text = documents.extract_text(filename, ct, data)
                if text is None:
                    entry["text"] = None
                    entry["note"] = "no text extractor for this file type"
                else:
                    entry["text"], entry["text_truncated"] = _truncate(
                        text, max_chars)
            result["files"].append(entry)

        return result

    # --- diagnostics --------------------------------------------------------

    def whoami(self) -> dict:
        session, sesskey = self._ensure()
        return {
            "base_url": self.base_url,
            "sesskey": sesskey,
            "timezone": str(self._tz),
            "course_count": len(self.get_courses()),
        }


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if text is None:
        return None, False
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + "\n…[truncated]", True
    return text, False
