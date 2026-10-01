"""Offline tests for document resolution (no network)."""

from ukmfolio_mcp import documents

BASE = "https://ukmfoliov2.ukm.my"


class _Resp:
    def __init__(self, text, status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.url = f"{BASE}/mod/url/view.php?id=1"


class _Session:
    def __init__(self, resp):
        self.resp = resp

    def get(self, *a, **kw):
        return self.resp


def test_url_module_link_inside_urlworkaround_div():
    # Markup as served by UKMFolio v2 when the url module does not redirect.
    html = ('<div role="main"><div class="urlworkaround">Click on '
            '<a href="https://forms.gle/abc?x=1&amp;y=2">Wednesday - Attendance</a>'
            ' to open the resource.</div></div>')
    out = documents.resolve_files(_Session(_Resp(html)), BASE, 1, "url")
    assert out["external_url"] == "https://forms.gle/abc?x=1&y=2"


def test_url_module_link_popup_fallback():
    html = "<script>window.open('https://example.com/x', 'popup');</script>"
    out = documents.resolve_files(_Session(_Resp(html)), BASE, 1, "url")
    assert out["external_url"] == "https://example.com/x"


def test_url_module_redirect():
    resp = _Resp("", 303, {"Location": "https://example.com/y"})
    out = documents.resolve_files(_Session(resp), BASE, 1, "url")
    assert out["external_url"] == "https://example.com/y"
