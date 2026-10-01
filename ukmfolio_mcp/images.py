"""Image discovery and retrieval for UKMFolio.

Teachers often post the real content of an announcement as an image (a poster,
a schedule, a WhatsApp-group QR code), so text extraction alone misses it.
This module:

  - finds teacher-posted images in Moodle HTML (forum posts, labels, section
    summaries, pages, activity descriptions) and in file lists (forum
    attachments, resource/folder files), skipping theme icons and site chrome;
  - downloads an image by URL and normalizes it for an AI client: converted to
    a format MCP clients accept (JPEG/PNG/GIF/WEBP) and downscaled so the long
    edge fits ``max_edge`` (large phone photos are several MB).

An image is identified everywhere by its ``image_url`` — the ``pluginfile.php``
URL on UKMFolio (or the original URL for externally hosted images). Pass that
to ``fetch_image`` / the ``view_image`` tool to get the pixels.
"""

from __future__ import annotations

import io
import ipaddress
import socket
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff",
              "heic", "heif", "svg"}

# pluginfile components that are site chrome, not course content.
_CHROME_MARKERS = (
    "/theme/", "/pix/", "/core_admin/", "/block_", "/user/icon/",
    "/badges/", "/core/", "/course/overviewfiles/",
)

_MAX_BYTES = 25 * 1024 * 1024
# Formats MCP clients (and Claude) can display as-is.
_PASSTHROUGH = {"JPEG": "jpeg", "PNG": "png", "GIF": "gif", "WEBP": "webp"}
# Re-encode anything larger than this even if it's within max_edge.
_REENCODE_BYTES = 3 * 1024 * 1024


# --- discovery --------------------------------------------------------------

def _filename_from_url(url: str) -> str:
    return unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "image"


def is_image_name(name: str | None, mimetype: str | None = None) -> bool:
    if mimetype and mimetype.lower().startswith("image/"):
        return True
    name = (name or "").lower()
    return "." in name and name.rsplit(".", 1)[-1] in IMAGE_EXTS


def _is_content_image(url: str) -> bool:
    """True for teacher content; False for icons/logos/theme assets."""
    if not url.startswith(("http://", "https://")):
        return False
    if "pluginfile.php" in url:
        return not any(m in url for m in _CHROME_MARKERS)
    # External image: keep unless it's obviously a Moodle theme/pix asset.
    return "/theme/image.php" not in url and "/pix/" not in url


def extract_images(html: str, base_url: str) -> list[dict]:
    """Return content images referenced by ``<img>`` tags or image links.

    Each item: ``{"image_url", "filename", "alt"}``. ``data:`` URIs and site
    chrome are skipped; results are de-duplicated by URL, in document order.
    """
    if not html or ("<img" not in html and "pluginfile" not in html):
        return []
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    seen: set[str] = set()

    def add(url: str, alt: str = ""):
        url = urljoin(base_url + "/", url.strip())
        if url in seen or not _is_content_image(url):
            return
        seen.add(url)
        out.append({"image_url": url, "filename": _filename_from_url(url),
                    "alt": alt.strip()})

    for img in soup.find_all("img"):
        src = img.get("src") or ""
        if src:
            add(src, img.get("alt") or img.get("title") or "")
    # Images posted as a link to the file rather than embedded.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "pluginfile.php" in href and is_image_name(_filename_from_url(href)):
            add(href, a.get_text(" ", strip=True))
    return out


def images_from_files(files: list[dict] | None) -> list[dict]:
    """Image entries from a Moodle file list (forum attachments etc.)."""
    out = []
    for f in files or []:
        url = f.get("fileurl") or f.get("url") or ""
        name = f.get("filename") or _filename_from_url(url)
        if url and is_image_name(name, f.get("mimetype")):
            out.append({"image_url": url, "filename": name, "alt": "",
                        "mimetype": f.get("mimetype")})
    return out


def text_with_image_markers(html: str, base_url: str) -> str:
    """Plain text of ``html`` with each content image replaced by a marker.

    ``<img alt="Poster" src=".../a.jpg">`` becomes ``[image: Poster | <url>]``
    so a reader of the text knows an image sits there and how to fetch it.
    """
    soup = BeautifulSoup(html or "", "lxml")
    for img in soup.find_all("img"):
        url = urljoin(base_url + "/", (img.get("src") or "").strip())
        if not _is_content_image(url):
            img.decompose()
            continue
        alt = (img.get("alt") or "").strip()
        label = f"{alt} | {url}" if alt else url
        img.replace_with(f" [image: {label}] ")
    return soup.get_text(" ", strip=True)


# --- retrieval --------------------------------------------------------------

def _check_public_host(url: str) -> None:
    """Refuse URLs that resolve to loopback/private addresses (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"unsupported image URL: {url}")
    for info in socket.getaddrinfo(parsed.hostname, None):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"refusing to fetch non-public address: {url}")


def _download(session, base_url: str, url: str) -> tuple[str, bytes]:
    """GET an image. UKMFolio URLs use the logged-in session; external URLs
    use a cookie-less request so credentials never leave UKMFolio."""
    from .moodle import SessionExpired  # deferred: moodle imports this module

    base_host = urlparse(base_url).netloc
    if urlparse(url).netloc == base_host:
        r = session.get(url, stream=True, timeout=60)
        if "/login/" in r.url:
            raise SessionExpired("notloggedin", "redirected to login page")
    else:
        for _ in range(4):  # follow redirects manually, re-checking each hop
            _check_public_host(url)
            r = requests.get(url, stream=True, timeout=60, allow_redirects=False,
                             headers={"User-Agent": session.headers.get("User-Agent", "")})
            if r.status_code not in (301, 302, 303, 307, 308):
                break
            url = urljoin(url, r.headers["Location"])
    r.raise_for_status()

    content_type = (r.headers.get("Content-Type") or "").split(";")[0].strip()
    data = bytearray()
    for chunk in r.iter_content(chunk_size=65536):
        data.extend(chunk)
        if len(data) > _MAX_BYTES:
            r.close()
            raise ValueError(f"image exceeds {_MAX_BYTES // (1024 * 1024)} MB cap")
    return content_type, bytes(data)


def fetch_image(session, base_url: str, url: str,
                max_edge: int = 1568) -> tuple[bytes, str, dict]:
    """Download an image and normalize it for an MCP client.

    Returns ``(data, format, info)`` where ``format`` is jpeg/png/gif/webp and
    ``info`` describes the original and returned image.
    """
    from PIL import Image, ImageOps

    content_type, raw = _download(session, base_url.rstrip("/"), url)
    if content_type.startswith("text/html"):
        raise ValueError(f"URL returned an HTML page, not an image: {url}")

    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except Exception as e:
        raise ValueError(
            f"not a decodable image ({content_type or 'unknown type'}): {e}") from e

    info = {
        "image_url": url,
        "filename": _filename_from_url(url),
        "original_format": im.format,
        "original_size": list(im.size),
        "original_bytes": len(raw),
    }

    fmt = _PASSTHROUGH.get(im.format or "")
    too_big = max(im.size) > max_edge
    if fmt and not too_big and len(raw) <= _REENCODE_BYTES:
        info.update(size=list(im.size), bytes=len(raw), resized=False)
        return raw, fmt, info

    im = ImageOps.exif_transpose(im)  # phone photos: honour rotation
    if getattr(im, "is_animated", False):
        im.seek(0)
    im.thumbnail((max_edge, max_edge))
    has_alpha = im.mode in ("RGBA", "LA") or (
        im.mode == "P" and "transparency" in im.info)
    buf = io.BytesIO()
    if has_alpha:
        im.convert("RGBA").save(buf, "PNG", optimize=True)
        fmt = "png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=85, optimize=True)
        fmt = "jpeg"
    data = buf.getvalue()
    info.update(size=list(im.size), bytes=len(data), resized=True)
    return data, fmt, info
