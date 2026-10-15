"""
Security Headers Middleware
OWASP A05 – Security Misconfiguration Remediation

Applies the following headers to every HTTP response:
  - Content-Security-Policy (CSP)
  - Strict-Transport-Security (HSTS)
  - X-Frame-Options
  - X-Content-Type-Options
  - Referrer-Policy
  - Permissions-Policy
  - X-XSS-Protection
  - Cache-Control (for sensitive endpoints)

Usage (Flask):
    from app.middleware.security_headers import SecurityHeadersMiddleware
    app = SecurityHeadersMiddleware(app)

Usage (Django settings.py):
    MIDDLEWARE = [
        'app.middleware.security_headers.DjangoSecurityHeadersMiddleware',
        ...
    ]

Usage (WSGI decorator):
    from app.middleware.security_headers import apply_security_headers
    @apply_security_headers
    def my_view(request): ...
"""

import os
import functools
from typing import Callable, Optional


# ─────────────────────────────────────────────
# Header Configuration
# ─────────────────────────────────────────────

class SecurityHeadersConfig:
    """Centralised, environment-aware security header configuration."""

    #: Set to True only in development to relax CSP for hot-reload
    DEV_MODE: bool = os.environ.get("APP_ENV", "production").lower() in ("development", "dev", "local")

    # Content Security Policy
    # Tightened to block inline scripts, allow only same-origin resources,
    # with explicit allowlist for trusted CDNs/APIs
    CSP_DIRECTIVES: dict = {
        "default-src": "'self'",
        "script-src": "'self'" + (" 'unsafe-inline' 'unsafe-eval'" if DEV_MODE else ""),
        "style-src": "'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src": "'self' https://fonts.gstatic.com",
        "img-src": "'self' data: https:",
        "connect-src": "'self'",
        "frame-src": "'none'",
        "frame-ancestors": "'none'",
        "form-action": "'self'",
        "base-uri": "'self'",
        "object-src": "'none'",
        "upgrade-insecure-requests": "",
        "block-all-mixed-content": "",
    }

    @classmethod
    def build_csp(cls) -> str:
        parts = []
        for directive, value in cls.CSP_DIRECTIVES.items():
            parts.append(f"{directive} {value}".strip())
        return "; ".join(parts)

    # Strict-Transport-Security
    # 2-year max-age, includeSubDomains, preload-ready
    HSTS: str = "max-age=63072000; includeSubDomains; preload"

    # X-Frame-Options: deny clickjacking
    X_FRAME_OPTIONS: str = "DENY"

    # X-Content-Type-Options: prevent MIME sniffing
    X_CONTENT_TYPE_OPTIONS: str = "nosniff"

    # Referrer-Policy: limit referrer leakage
    REFERRER_POLICY: str = "strict-origin-when-cross-origin"

    # Permissions-Policy: disable unused browser features
    PERMISSIONS_POLICY: str = (
        "geolocation=(), microphone=(), camera=(), payment=(), "
        "usb=(), magnetometer=(), gyroscope=(), accelerometer=()"
    )

    # X-XSS-Protection (legacy browsers)
    X_XSS_PROTECTION: str = "1; mode=block"

    # Cache-Control for auth/sensitive endpoints
    CACHE_CONTROL_PRIVATE: str = "no-store, no-cache, must-revalidate, private"


def get_security_headers(
    include_hsts: bool = True,
    custom_csp: Optional[str] = None,
) -> dict:
    """
    Return a dict of security headers suitable for any HTTP framework.

    Args:
        include_hsts: Set to False for non-HTTPS development environments.
        custom_csp: Override the default Content-Security-Policy.

    Returns:
        dict mapping header name → header value.
    """
    cfg = SecurityHeadersConfig
    headers = {
        "Content-Security-Policy": custom_csp or cfg.build_csp(),
        "X-Frame-Options": cfg.X_FRAME_OPTIONS,
        "X-Content-Type-Options": cfg.X_CONTENT_TYPE_OPTIONS,
        "Referrer-Policy": cfg.REFERRER_POLICY,
        "Permissions-Policy": cfg.PERMISSIONS_POLICY,
        "X-XSS-Protection": cfg.X_XSS_PROTECTION,
    }
    if include_hsts and not cfg.DEV_MODE:
        headers["Strict-Transport-Security"] = cfg.HSTS
    return headers


# ─────────────────────────────────────────────
# Flask WSGI Middleware
# ─────────────────────────────────────────────

class SecurityHeadersMiddleware:
    """
    WSGI middleware that injects security headers into every HTTP response.

    Compatible with Flask, Django, Starlette (via WSGIMiddleware), and any
    PEP 3333-compliant WSGI application.

    Example::

        from flask import Flask
        from app.middleware.security_headers import SecurityHeadersMiddleware

        app = Flask(__name__)
        app.wsgi_app = SecurityHeadersMiddleware(app.wsgi_app)
    """

    # Endpoints that carry sensitive data and should never be cached
    PRIVATE_PATHS: tuple = (
        "/api/auth",
        "/api/token",
        "/api/user",
        "/admin",
        "/login",
        "/logout",
        "/register",
        "/password",
    )

    def __init__(self, app, include_hsts: bool = True):
        self.app = app
        self.include_hsts = include_hsts
        self._headers = get_security_headers(include_hsts=include_hsts)

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        is_private = any(path.startswith(p) for p in self.PRIVATE_PATHS)

        def _start_response(status, headers, exc_info=None):
            headers_dict = dict(headers)

            # Inject security headers (do not overwrite existing)
            for name, value in self._headers.items():
                header_key = name.lower()
                existing = any(h[0].lower() == header_key for h in headers)
                if not existing:
                    headers.append((name, value))

            # Add private cache-control for sensitive endpoints
            if is_private:
                cc_key = "cache-control"
                if not any(h[0].lower() == cc_key for h in headers):
                    headers.append(("Cache-Control", SecurityHeadersConfig.CACHE_CONTROL_PRIVATE))

            return start_response(status, headers, exc_info)

        return self.app(environ, _start_response)


# ─────────────────────────────────────────────
# Flask Blueprint Extension (apply_security_headers)
# ─────────────────────────────────────────────

def apply_security_headers(f: Callable) -> Callable:
    """
    Flask view decorator that adds security headers to a single endpoint.

    Usage::

        from app.middleware.security_headers import apply_security_headers

        @app.route('/api/data')
        @apply_security_headers
        def get_data():
            return jsonify(data)
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        # Import here to avoid hard Flask dependency in non-Flask environments
        try:
            from flask import g, request, make_response
            response = make_response(f(*args, **kwargs))
        except ImportError:
            return f(*args, **kwargs)

        headers = get_security_headers()
        for name, value in headers.items():
            response.headers.setdefault(name, value)
        return response

    return decorated


def init_flask_app(app):
    """
    Register security headers on a Flask app using after_request hook.

    Usage::

        from flask import Flask
        from app.middleware.security_headers import init_flask_app

        app = Flask(__name__)
        init_flask_app(app)
    """
    @app.after_request
    def _add_security_headers(response):
        headers = get_security_headers()
        for name, value in headers.items():
            response.headers.setdefault(name, value)
        return response

    return app


# ─────────────────────────────────────────────
# Django Middleware
# ─────────────────────────────────────────────

class DjangoSecurityHeadersMiddleware:
    """
    Django-compatible security headers middleware.

    Add to Django MIDDLEWARE list (before other middlewares for coverage):

        MIDDLEWARE = [
            'app.middleware.security_headers.DjangoSecurityHeadersMiddleware',
            ...
        ]
    """

    PRIVATE_PATHS: tuple = (
        "/api/auth",
        "/api/token",
        "/api/user",
        "/admin",
        "/login",
        "/logout",
        "/register",
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self._headers = get_security_headers()

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path

        for name, value in self._headers.items():
            if name not in response:
                response[name] = value

        if any(path.startswith(p) for p in self.PRIVATE_PATHS):
            if "Cache-Control" not in response:
                response["Cache-Control"] = SecurityHeadersConfig.CACHE_CONTROL_PRIVATE

        return response