import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app.api.auth import SignupRequest
from app.core.config import settings
from app.services.auth import hash_password, verify_password, verify_request_origin


def make_request(headers=None, scheme="http", server=("testserver", 80)):
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "POST",
        "scheme": scheme,
        "server": server,
        "path": "/api/login",
        "headers": raw_headers,
    }
    return Request(scope)


class AuthenticationTests(unittest.TestCase):
    def test_password_hash_verifies_only_matching_password(self):
        encoded = hash_password("correct horse battery staple")
        self.assertNotEqual(encoded, "correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", encoded))
        self.assertFalse(verify_password("incorrect password", encoded))

    def test_signup_normalizes_email_and_does_not_accept_user_type(self):
        payload = SignupRequest(
            email="  Client@Example.com ",
            name=" Client Name ",
            password="correct horse battery staple",
        )
        self.assertEqual(payload.email, "client@example.com")
        self.assertEqual(payload.name, "Client Name")
        self.assertNotIn("user_type", payload.model_dump())

    def test_signup_rejects_user_type_override(self):
        with self.assertRaises(ValueError):
            SignupRequest(
                email="client@example.com",
                name="Client",
                password="correct horse battery staple",
                user_type="admin",
            )

    def test_signup_rejects_short_password(self):
        with self.assertRaises(ValueError):
            SignupRequest(
                email="client@example.com",
                name="Client",
                password="short",
            )

    def test_verify_request_origin_allows_configured_origin(self):
        req = make_request(headers={"Origin": settings.allowed_origins[0]})
        verify_request_origin(req)  # Should not raise

    def test_verify_request_origin_allows_same_origin_from_host(self):
        req = make_request(
            headers={"Origin": "https://travelforge.onrender.com", "Host": "travelforge.onrender.com"}
        )
        verify_request_origin(req)  # Same host matches origin

    def test_verify_request_origin_allows_same_origin_from_proxy_headers(self):
        req = make_request(
            headers={
                "Origin": "https://travelforge.onrender.com",
                "Host": "10.0.0.1:10000",
                "X-Forwarded-Host": "travelforge.onrender.com",
                "X-Forwarded-Proto": "https",
            }
        )
        verify_request_origin(req)  # Forwarded host matches origin

    def test_verify_request_origin_handles_trailing_slash(self):
        req = make_request(
            headers={"Origin": f"{settings.allowed_origins[0]}/"}
        )
        verify_request_origin(req)  # Normalized and matches

    def test_verify_request_origin_allows_via_referer_fallback(self):
        req = make_request(
            headers={"Referer": "https://travelforge.onrender.com/login", "Host": "travelforge.onrender.com"}
        )
        verify_request_origin(req)

    def test_verify_request_origin_rejects_unknown_origin(self):
        req = make_request(headers={"Origin": "https://malicious-site.com"})
        with self.assertRaises(HTTPException) as ctx:
            verify_request_origin(req)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.detail, "Request origin is not allowed.")

    def test_verify_request_origin_production_requires_origin(self):
        with patch.object(settings, "ENVIRONMENT", "production"):
            req = make_request(headers={})
            with self.assertRaises(HTTPException) as ctx:
                verify_request_origin(req)
            self.assertEqual(ctx.exception.status_code, 403)
            self.assertEqual(ctx.exception.detail, "An allowed request origin is required.")
