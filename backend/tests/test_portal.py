import unittest

from fastapi.testclient import TestClient

from app.main import app


class PortalStaticRouteTests(unittest.TestCase):
    def test_portal_and_assets_are_served(self):
        client = TestClient(app)
        try:
            page = client.get("/")
            script = client.get("/static/app.js")
            alpine = client.get("/static/alpine.min.js")
            stylesheet = client.get("/static/styles.css")
        finally:
            client.close()

        self.assertEqual(page.status_code, 200)
        self.assertIn('x-data="portal"', page.text)
        self.assertIn('@submit.prevent="submitAuth"', page.text)
        self.assertIn('/static/alpine.min.js', page.text)
        self.assertEqual(script.status_code, 200)
        self.assertIn('window.Alpine.data("portal"', script.text)
        self.assertEqual(alpine.status_code, 200)
        self.assertIn("Alpine", alpine.text)
        self.assertEqual(stylesheet.status_code, 200)
        self.assertIn(".case-layout", stylesheet.text)


if __name__ == "__main__":
    unittest.main()
