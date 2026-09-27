"""UKMFolio MCP Server.

Exposes UKM Folio (Moodle LMS) content — courses, assignment/quiz deadlines,
forum announcements and course documents — to AI agents over the Model Context
Protocol.

Authentication and Moodle access logic are adapted from the proven
UKMFolioPuller project (SAML 2.0 SSO via sso.ukm.my + Moodle AJAX endpoints).
"""

__version__ = "0.1.0"
