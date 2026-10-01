"""Offline tests for image discovery and retrieval (no network access)."""

import io

import pytest
from PIL import Image

from ukmfolio_mcp import client as client_mod
from ukmfolio_mcp import documents, images, moodle

BASE = "https://ukmfoliov2.ukm.my"
POSTER = f"{BASE}/pluginfile.php/14955/mod_forum/post/287/poster.jpg"


# --- discovery --------------------------------------------------------------

def test_extract_images_skips_chrome_and_data_uris():
    html = f"""
      <img src="{BASE}/pluginfile.php/1/core_admin/logo/0x200/1/logo.png">
      <img src="{BASE}/pluginfile.php/6886/block_html/content/sdg.png">
      <img src="{BASE}/theme/image.php/boost/core/1/i/edit">
      <img src="data:image/png;base64,AAAA">
      <img src="{POSTER}" alt="No tutorial this week">
      <img src="{POSTER}" alt="duplicate">
      <img src="/pluginfile.php/9/mod_label/intro/rel.png">
      <a href="{BASE}/pluginfile.php/9/mod_page/content/1/Schedule%201.PNG">schedule</a>
      <a href="{BASE}/pluginfile.php/9/mod_page/content/1/notes.pdf">notes</a>
    """
    found = images.extract_images(html, BASE)
    assert [f["image_url"] for f in found] == [
        POSTER,
        f"{BASE}/pluginfile.php/9/mod_label/intro/rel.png",
        f"{BASE}/pluginfile.php/9/mod_page/content/1/Schedule%201.PNG",
    ]
    assert found[0]["alt"] == "No tutorial this week"
    assert found[2]["filename"] == "Schedule 1.PNG"
    assert found[2]["alt"] == "schedule"


def test_text_with_image_markers():
    html = (f'<p>Scan the QR below.</p><img src="{POSTER}" alt="QR">'
            f'<img src="{BASE}/theme/image.php/boost/core/1/s/smiley">')
    text = images.text_with_image_markers(html, BASE)
    assert text == f"Scan the QR below. [image: QR | {POSTER}]"


def test_post_images_merges_body_attachments_inline():
    post = {
        "message": f'<img src="{POSTER}" alt="a">',
        "attachments": [
            {"filename": "timetable.png", "mimetype": "image/png",
             "fileurl": f"{BASE}/pluginfile.php/1/mod_forum/attachment/2/timetable.png"},
            {"filename": "slides.pdf", "mimetype": "application/pdf",
             "fileurl": f"{BASE}/pluginfile.php/1/mod_forum/attachment/2/slides.pdf"},
        ],
        "messageinlinefiles": [
            {"filename": "poster.jpg", "mimetype": "image/jpeg", "fileurl": POSTER},
        ],
    }
    found = moodle._post_images(post, BASE)
    assert [f["filename"] for f in found] == ["poster.jpg", "timetable.png"]


# --- course content scan ----------------------------------------------------

class FakeResp:
    def __init__(self, text="", url="", status=200, headers=None, content=b""):
        self.text = text
        self.url = url
        self.status_code = status
        self.headers = headers or {}
        self._content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)

    def iter_content(self, chunk_size):
        yield self._content

    def close(self):
        pass


def test_get_course_images_covers_sections_labels_pages_resources(monkeypatch):
    state = {
        "section": [
            {"id": "10", "title": "Week 1", "hassummary": True,
             "sectionurl": f"{BASE}/course/section.php?id=10"},
            {"id": "11", "title": "Week 2", "hassummary": False},
        ],
        "cm": [
            {"id": "100", "name": "Notice", "sectionid": "10", "url": ""},
            {"id": "101", "name": "Week 1 page", "sectionid": "10",
             "url": f"{BASE}/mod/page/view.php?id=101"},
            {"id": "102", "name": "Timetable", "sectionid": "11",
             "url": f"{BASE}/mod/resource/view.php?id=102"},
            {"id": "103", "name": "Slides", "sectionid": "11",
             "url": f"{BASE}/mod/resource/view.php?id=103"},
            {"id": "104", "name": "Forum", "sectionid": "10",
             "url": f"{BASE}/mod/forum/view.php?id=104"},
        ],
    }
    section_html = f"""
      <li data-for="section" data-id="10"><div data-for="sectioninfo">
        <img src="{BASE}/pluginfile.php/5/course/section/10/banner.png" alt="Week 1">
      </div><ul><li data-for="cmitem">
        <img src="{BASE}/pluginfile.php/5/mod_label/intro/inside_cm.png">
      </li></ul></li>"""
    page_html = (f'<div id="region-main"><img src="{BASE}/pluginfile.php/6/'
                 f'mod_page/content/1/diagram.png"></div>'
                 f'<img src="{BASE}/pluginfile.php/1/core_admin/logo/x.png">')

    class Session:
        def get(self, url, params=None, allow_redirects=True, timeout=None):
            if "section.php" in url:
                return FakeResp(section_html, url)
            if "/mod/page/" in url:
                return FakeResp(page_html, url)
            if "/mod/resource/" in url:
                name = "timetable.jpg" if params["id"] == 102 else "slides.pdf"
                return FakeResp(status=303, url=url, headers={
                    "Location": f"{BASE}/pluginfile.php/7/mod_resource/content/1/{name}"})
            raise AssertionError(url)

    def fake_ajax(session, sesskey, base_url, method, args):
        if method == "core_courseformat_get_state":
            return state
        if method == "core_course_get_module":
            assert args["id"] == 100  # forum/page/resource aren't rendered as cards
            return f'<li><img src="{BASE}/pluginfile.php/8/mod_label/intro/notice.png"></li>'
        raise AssertionError(method)

    monkeypatch.setattr(moodle, "_ajax_call", fake_ajax)
    found = moodle.get_course_images(Session(), "k", BASE, 1)
    got = [(f["source"], f["filename"], f.get("cmid")) for f in found]
    assert got == [
        ("section", "banner.png", None),
        ("label", "notice.png", 100),
        ("page", "diagram.png", 101),
        ("resource", "timetable.jpg", 102),
    ]


def test_read_document_flags_image_files_without_downloading(monkeypatch):
    c = client_mod.UKMFolioClient.__new__(client_mod.UKMFolioClient)
    c.base_url = BASE
    c._call = lambda fn: fn(None, None)
    monkeypatch.setattr(documents, "resolve_files", lambda *a: {
        "type": "folder", "external_url": None, "page_text": None, "images": [],
        "files": [{"filename": "map.png", "file_url": f"{BASE}/pluginfile.php/1/map.png"}],
    })
    monkeypatch.setattr(documents, "download_file",
                        lambda *a: pytest.fail("image should not be downloaded"))
    out = c.get_document_content(5, "folder")
    assert out["files"][0]["is_image"] is True
    assert "view_image" in out["files"][0]["note"]


# --- retrieval --------------------------------------------------------------

def _encode(im, fmt):
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


class ImageSession:
    headers = {"User-Agent": "test"}

    def __init__(self, data, content_type="image/jpeg", url=POSTER):
        self.data, self.ct, self.url = data, content_type, url

    def get(self, url, stream=False, timeout=None):
        return FakeResp(url=self.url, headers={"Content-Type": self.ct},
                        content=self.data)


def test_fetch_image_passthrough_small_jpeg():
    raw = _encode(Image.new("RGB", (300, 200), "red"), "JPEG")
    data, fmt, info = images.fetch_image(ImageSession(raw), BASE, POSTER)
    assert data == raw and fmt == "jpeg" and info["resized"] is False


def test_fetch_image_downscales_large_photo():
    raw = _encode(Image.new("RGB", (1536, 2752), "blue"), "JPEG")
    data, fmt, info = images.fetch_image(ImageSession(raw), BASE, POSTER)
    out = Image.open(io.BytesIO(data))
    assert fmt == "jpeg" and info["resized"] is True
    assert max(out.size) == 1568 and info["original_size"] == [1536, 2752]


def test_fetch_image_converts_bmp_and_keeps_alpha_as_png():
    bmp = _encode(Image.new("RGB", (40, 40)), "BMP")
    _, fmt, _ = images.fetch_image(ImageSession(bmp, "image/bmp"), BASE, POSTER)
    assert fmt == "jpeg"
    tiff = _encode(Image.new("RGBA", (40, 40), (0, 0, 0, 0)), "TIFF")
    data, fmt, _ = images.fetch_image(ImageSession(tiff, "image/tiff"), BASE, POSTER)
    assert fmt == "png" and Image.open(io.BytesIO(data)).mode == "RGBA"


def test_fetch_image_detects_expired_session():
    sess = ImageSession(b"<html>login</html>", "text/html",
                        url=f"{BASE}/login/index.php")
    with pytest.raises(moodle.SessionExpired):
        images.fetch_image(sess, BASE, POSTER)


def test_fetch_image_refuses_private_external_hosts():
    with pytest.raises(ValueError, match="non-public"):
        images.fetch_image(ImageSession(b""), BASE, "http://127.0.0.1/x.png")
    with pytest.raises(ValueError, match="unsupported"):
        images.fetch_image(ImageSession(b""), BASE, "file:///etc/passwd")
