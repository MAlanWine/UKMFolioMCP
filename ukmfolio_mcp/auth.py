"""UKMFolio SAML 2.0 SSO login.

Adapted from the proven UKMFolioPuller ``auth.py``. UKMFolio uses SAML SSO
(SimpleSAMLphp IdP at sso.ukm.my), NOT Moodle web-service tokens.

Login flow:
1. GET  ukmfolio.ukm.my/login/index.php   -> 302 -> sso.ukm.my SSOService.php?SAMLRequest=...
2. GET  SSOService.php                     -> 302 -> loginuserpass.php?AuthState=...
3. GET  loginuserpass.php                  -> 200 -> login form (extract AuthState)
4. POST loginuserpass.php (credentials)    -> 200 -> auto-submit form (SAMLResponse)
5. POST saml2-acs.php (SAMLResponse)       -> 302 -> login complete
6. Extract sesskey from an authenticated page for subsequent AJAX calls
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import requests

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class _FormParser(HTMLParser):
    """Parse HTML forms, extracting action/method and all hidden inputs."""

    def __init__(self):
        super().__init__()
        self.forms: list[dict] = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form":
            self._current = {
                "action": a.get("action", ""),
                "method": a.get("method", "get").upper(),
                "fields": {},
            }
        elif tag == "input" and self._current is not None:
            name = a.get("name")
            if name:
                self._current["fields"][name] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def _parse_forms(html: str) -> list[dict]:
    parser = _FormParser()
    parser.feed(html)
    return parser.forms


def _extract_sesskey(html: str) -> str | None:
    """Extract the Moodle sesskey from page HTML."""
    match = re.search(r'"sesskey"\s*:\s*"([a-zA-Z0-9]+)"', html)
    if match:
        return match.group(1)
    match = re.search(r'name="sesskey"\s+value="([a-zA-Z0-9]+)"', html)
    if match:
        return match.group(1)
    return None


def new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def login(config: dict) -> tuple[requests.Session, str]:
    """Perform the full SAML login flow.

    Args:
        config: dict with ``username``, ``password``, ``base_url``, ``sso_url``.

    Returns:
        ``(session, sesskey)`` — authenticated session + Moodle sesskey.
    """
    session = new_session()
    base_url = config["base_url"].rstrip("/")
    sso_url = config["sso_url"].rstrip("/")

    # Step 1: Moodle login page -> redirect to IdP
    resp = session.get(f"{base_url}/login/index.php", allow_redirects=False)
    if resp.status_code != 302:
        raise RuntimeError(f"Step 1 failed: expected 302, got {resp.status_code}")
    idp_url = resp.headers["Location"]

    # Step 2: IdP SSO endpoint -> redirect to login form
    resp = session.get(idp_url, allow_redirects=False)
    if resp.status_code != 302:
        raise RuntimeError(f"Step 2 failed: expected 302, got {resp.status_code}")
    login_form_url = resp.headers["Location"]
    if login_form_url.startswith("/"):
        login_form_url = sso_url + login_form_url

    # Step 3: Load login form, extract AuthState
    resp = session.get(login_form_url)
    resp.raise_for_status()
    auth_state = None
    for form in _parse_forms(resp.text):
        if "AuthState" in form["fields"]:
            auth_state = form["fields"]["AuthState"]
            break
    if auth_state is None:
        raise RuntimeError("Step 3 failed: could not extract AuthState")

    # Step 4: Submit credentials
    resp = session.post(
        login_form_url,
        data={
            "username": config["username"],
            "password": config["password"],
            "AuthState": auth_state,
        },
    )
    resp.raise_for_status()
    if "Incorrect username or password" in resp.text or "loginerror" in resp.text:
        raise RuntimeError("Login failed: incorrect username or password")

    # Step 5: Extract SAMLResponse auto-submit form
    saml_form = None
    for form in _parse_forms(resp.text):
        if "SAMLResponse" in form["fields"]:
            saml_form = form
            break
    if saml_form is None:
        raise RuntimeError("Step 5 failed: SAMLResponse form not found")

    # Step 6: POST SAMLResponse to Moodle ACS
    resp = session.post(
        saml_form["action"],
        data={
            "SAMLResponse": saml_form["fields"]["SAMLResponse"],
            "RelayState": saml_form["fields"].get("RelayState", ""),
        },
    )
    resp.raise_for_status()

    # Step 7: Extract sesskey
    sesskey = _extract_sesskey(resp.text)
    if sesskey is None:
        resp = session.get(base_url)
        sesskey = _extract_sesskey(resp.text)
    if sesskey is None:
        raise RuntimeError("Login succeeded but sesskey could not be extracted")

    return session, sesskey
