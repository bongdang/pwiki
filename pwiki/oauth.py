"""Google OAuth (OIDC) integration via Authlib.

Enabled only when both GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET are set.
When they are not set, oauth_enabled() returns False and route handlers show guidance.
"""

from __future__ import annotations

from typing import Optional

from authlib.integrations.flask_client import OAuth
from loguru import logger

import config


GOOGLE_DISCOVERY_URL = 'https://accounts.google.com/.well-known/openid-configuration'

_oauth: Optional[OAuth] = None


def oauth_enabled() -> bool:
    return bool(config.GOOGLE_OAUTH_CLIENT_ID and config.GOOGLE_OAUTH_CLIENT_SECRET)


def init_oauth(app) -> Optional[OAuth]:
    """Register the Google OAuth client on the Flask app. Return None when disabled."""
    global _oauth
    if not oauth_enabled():
        logger.info("oauth disabled (GOOGLE_OAUTH_CLIENT_ID/SECRET not set)")
        return None
    oauth = OAuth(app)
    oauth.register(
        name='google',
        client_id=config.GOOGLE_OAUTH_CLIENT_ID,
        client_secret=config.GOOGLE_OAUTH_CLIENT_SECRET,
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_kwargs={'scope': 'openid email profile'},
    )
    _oauth = oauth
    logger.info("oauth enabled (provider=google)")
    return oauth


def get_oauth() -> Optional[OAuth]:
    return _oauth


def parse_userinfo(token: dict) -> dict:
    """Extract user information from authorize_access_token().

    With OIDC discovery, Authlib parses and validates the id_token
    (aud/iss/exp) automatically and exposes the result as token['userinfo'].
    Some fields also fall back to top-level token keys for incomplete responses.
    """
    info = token.get('userinfo') or {}
    sub   = info.get('sub')   or token.get('sub')
    email = (info.get('email') or token.get('email') or '').strip().lower()
    name  = info.get('name')  or token.get('name')
    email_verified = info.get('email_verified', token.get('email_verified', True))
    if not sub or not email:
        raise ValueError('id_token missing required claims (sub/email)')
    if email_verified is False or str(email_verified).lower() == 'false':
        raise ValueError('id_token email is not verified')
    return {'sub': sub, 'email': email, 'name': name}
