import unittest

from app.api.auth import SignupRequest
from app.services.auth import hash_password, verify_password


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
