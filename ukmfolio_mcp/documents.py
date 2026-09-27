"""Document resolution, download and text extraction for UKMFolio.

A "document" maps to a Moodle course module. Resolution depends on the module
type (verified live against UKMFolio):

  - resource: ``/mod/resource/view.php?id=<cmid>`` 303-redirects straight to the
              ``pluginfile.php`` file (PDF, etc.).
  - folder:   ``/mod/folder/view.php?id=<cmid>`` renders a page listing one
              ``pluginfile.php`` link per contained file.
  - url:      external link (no download).
  - page:     in-Moodle HTML content (extract page text).
  - book:     multi-chapter HTML (extract text of all chapters).

Text extraction supports PDF, DOCX, PPTX, XLSX, plain text and HTML. Unknown
binary types are downloaded but returned without extracted text.
"""

from __future__ import annotations

import io
import re
from urllib.parse import unquote, urlparse

import requests

from .moodle import SessionExpired

_PLUGINFILE_RE = re.compile(r"https?://[^\s\"'<>\\]*?/pluginfile\.php/[^\s\"'<>\\]*")
_MAX_BYTES = 25 * 1024 * 1024  # 25 MB safety cap per file


# --- file resolution --------------------------------------------------------

def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = path.rsplit("/", 1)[-1]
    return unquote(name) or "file"


def _pluginfile_links(html: str) -> list[str]:
    seen: list[str] = []
    for m in _PLUGINFILE_RE.findall(html):
        link = m.split("&amp;")[0] if "&amp;" in m else m
        if link not in seen:
            seen.append(link)
    return seen


def _check_login_redirect(resp: requests.Response) -> None:
    if "/login/index.php" in resp.url:
        raise SessionExpired("notloggedin", "redirected to login page")


def resolve_files(session, base_url, cmid: int, module_type: str) -> dict:
    """Resolve a course-module cmid to its downloadable file URLs / page text.

    Returns a dict:
      {"type": <module_type>, "files": [{"filename","file_url"}...],
       "external_url": <str|None>, "page_text": <str|None>}
    """
    base_url = base_url.rstrip("/")
    result = {"type": module_type, "files": [], "external_url": None,
              "page_text": None}

    if module_type == "resource":
        r = session.get(f"{base_url}/mod/resource/view.php",
                        params={"id": cmid}, allow_redirects=False, timeout=30)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers["Location"]
            if "/login/" in loc:
                raise SessionExpired("notloggedin", "redirected to login page")
            result["files"].append(
                {"filename": _filename_from_url(loc), "file_url": loc})
        else:
            _check_login_redirect(r)
            for link in _pluginfile_links(r.text):
                result["files"].append(
                    {"filename": _filename_from_url(link), "file_url": link})

    elif module_type == "folder":
        r = session.get(f"{base_url}/mod/folder/view.php",
                        params={"id": cmid}, timeout=30)
        r.raise_for_status()
        _check_login_redirect(r)
        for link in _pluginfile_links(r.text):
            result["files"].append(
                {"filename": _filename_from_url(link), "file_url": link})

    elif module_type == "url":
        r = session.get(f"{base_url}/mod/url/view.php",
                        params={"id": cmid}, allow_redirects=False, timeout=30)
        if r.status_code in (301, 302, 303, 307, 308):
            result["external_url"] = r.headers.get("Location")
        else:
            _check_login_redirect(r)
            m = re.search(r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*urlworkaround',
                          r.text)
            if not m:
                m = re.search(r'window\.open\(["\']([^"\']+)["\']', r.text)
            if m:
                result["external_url"] = m.group(1)

    elif module_type in ("page", "book"):
        view = "page" if module_type == "page" else "book"
        r = session.get(f"{base_url}/mod/{view}/view.php",
                        params={"id": cmid}, timeout=30)
        r.raise_for_status()
        _check_login_redirect(r)
        result["page_text"] = _extract_main_html_text(r.text)

    return result


# --- download ---------------------------------------------------------------

def download_file(session, file_url: str) -> tuple[str, str, bytes]:
    """Download a file URL. Returns ``(filename, content_type, data)``."""
    r = session.get(file_url, stream=True, timeout=60)
    r.raise_for_status()
    _check_login_redirect(r)

    content_type = (r.headers.get("Content-Type") or "").split(";")[0].strip()

    filename = None
    disp = r.headers.get("Content-Disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disp)
    if m:
        filename = unquote(m.group(1).strip())
    if not filename:
        filename = _filename_from_url(file_url)

    chunks = bytearray()
    for chunk in r.iter_content(chunk_size=65536):
        chunks.extend(chunk)
        if len(chunks) > _MAX_BYTES:
            r.close()
            raise ValueError(
                f"File exceeds {_MAX_BYTES // (1024 * 1024)} MB cap: {filename}")
    return filename, content_type, bytes(chunks)


# --- text extraction --------------------------------------------------------

def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def extract_text(filename: str, content_type: str, data: bytes) -> str | None:
    """Extract plain text from a downloaded file, or None if unsupported."""
    ext = _ext(filename)
    ct = (content_type or "").lower()

    try:
        if ext == "pdf" or "pdf" in ct:
            return _extract_pdf(data)
        if ext == "docx" or "wordprocessingml" in ct:
            return _extract_docx(data)
        if ext == "pptx" or "presentationml" in ct:
            return _extract_pptx(data)
        if ext == "xlsx" or "spreadsheetml" in ct:
            return _extract_xlsx(data)
        if ext in ("txt", "md", "csv", "log", "json", "xml") or ct.startswith("text/plain"):
            return data.decode("utf-8", errors="replace")
        if ext in ("html", "htm") or "html" in ct:
            return _extract_main_html_text(data.decode("utf-8", errors="replace"))
    except Exception as e:  # extraction is best-effort
        return f"[text extraction failed: {type(e).__name__}: {e}]"

    return None


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n\n".join(parts).strip()


def _extract_docx(data: bytes) -> str:
    import docx

    doc = docx.Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
    return "\n".join(parts).strip()


def _extract_pptx(data: bytes) -> str:
    from pptx import Presentation

    prs = Presentation(io.BytesIO(data))
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        slide_parts = [f"--- Slide {i} ---"]
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = "".join(run.text for run in para.runs)
                    if text.strip():
                        slide_parts.append(text)
        parts.append("\n".join(slide_parts))
    return "\n\n".join(parts).strip()


def _extract_xlsx(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"--- Sheet: {ws.title} ---")
        for row in ws.iter_rows(values_only=True):
            cells = ["" if v is None else str(v) for v in row]
            if any(cells):
                parts.append("\t".join(cells))
    wb.close()
    return "\n".join(parts).strip()


def _extract_main_html_text(html: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    main = (soup.find(attrs={"role": "main"})
            or soup.find(id="region-main")
            or soup.body
            or soup)
    return main.get_text("\n", strip=True)
