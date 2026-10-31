import shutil
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

STATIC_DIR = Path(__file__).resolve().parents[1] / "app" / "static"


class PortalStaticRouteTests(unittest.TestCase):
    def test_portal_and_assets_are_served(self):
        client = TestClient(app)
        try:
            page = client.get("/")
            script = client.get("/static/app.js")
            stylesheet = client.get("/static/styles.css")
            removed_alpine = client.get("/static/alpine.min.js")
        finally:
            client.close()

        self.assertEqual(page.status_code, 200)
        self.assertIn('id="auth-screen"', page.text)
        self.assertIn('id="auth-form"', page.text)
        self.assertIn('/static/app.js', page.text)
        self.assertIn('<template id="tpl-case-card">', page.text)
        self.assertNotIn("alpine", page.text.lower())
        self.assertNotIn("x-data", page.text)

        self.assertEqual(script.status_code, 200)
        self.assertIn('"use strict"', script.text)
        self.assertIn("DOMContentLoaded", script.text)
        self.assertIn("/auth/login", script.text)
        self.assertIn("/staff/cases", script.text)
        self.assertNotIn("Alpine", script.text)

        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(".case-layout", stylesheet.text)
        self.assertNotIn("x-cloak", stylesheet.text)

        self.assertEqual(removed_alpine.status_code, 404)

    def test_frontend_script_has_valid_javascript_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed.")
        result = subprocess.run(
            [node, "--check", str(STATIC_DIR / "app.js")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
