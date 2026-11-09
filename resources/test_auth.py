"""Run only against a disposable PostgreSQL database: AUTH_TEST_DATABASE=1."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import check_password_hash


@unittest.skipUnless(os.getenv("AUTH_TEST_DATABASE") == "1", "Requires an isolated test database")
class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv("DB_NAME") != "cpe_auth_tests":
            raise RuntimeError("Authentication tests require the disposable database cpe_auth_tests.")
        os.environ.update(
            DASHBOARD_SECRET_KEY="authentication-test-signing-key-at-least-32-characters",
            DASHBOARD_ADMIN_PASSWORD="Initial-test-password-123",
            AUTH_COOKIE_SECURE="false",
            HUAWEI_ROUTER_PASSWORD="router-test-only",
        )
        import app
        import auth
        cls.app = app.app
        cls.auth = auth
        cls.store = auth.store
        cls.store.initialize()

    def setUp(self):
        with self.store.transaction() as cur:
            cur.execute("TRUNCATE dashboard_accounts, dashboard_auth_limits RESTART IDENTITY CASCADE")
        self.store.ready = False
        self.store.initialize()
        self.client = self.app.test_client()

    def token(self, client, path="/login"):
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        with client.session_transaction() as session:
            return session["csrf_token"]

    def login(self, client, username="admin", password="Initial-test-password-123"):
        return client.post("/login", data={
            "csrf_token": self.token(client), "username": username, "password": password,
        })

    def request_user(self, username="newuser"):
        return self.client.post("/request-account", data={
            "csrf_token": self.token(self.client, "/request-account"),
            "username": username, "full_name": "New User", "email": "user@example.test",
            "reason": "Router access", "password": "User-test-password-123",
            "confirm_password": "User-test-password-123", "role": "admin", "status": "active",
        })

    def test_dashboard_and_apis_require_login_but_health_remains_public(self):
        self.assertEqual(self.client.get("/").status_code, 302)
        for url in ("/api/routers", "/api/cellular", "/api/history", "/api/cell-tags", "/api/speedtest"):
            self.assertEqual(self.client.get(url).status_code, 401, url)
        self.assertEqual(self.client.post("/api/speedtest/run").status_code, 401)
        self.assertEqual(self.client.get("/health").json, {"status": "ok"})

    def test_request_is_pending_and_only_admin_can_approve(self):
        self.assertEqual(self.request_user().status_code, 302)
        user = self.store.by_username("newuser")
        self.assertEqual(user["status"], "pending")
        self.assertEqual(user["role"], "viewer")
        self.assertNotEqual(user["password_hash"], "User-test-password-123")
        self.assertTrue(check_password_hash(user["password_hash"], "User-test-password-123"))
        self.assertEqual(self.login(self.client, "newuser", "User-test-password-123").status_code, 401)
        admin = self.app.test_client()
        self.assertEqual(self.login(admin).status_code, 302)
        token = self.token(admin, "/admin/account-requests")
        self.assertEqual(admin.post(f"/admin/account-requests/{user['id']}", data={
            "csrf_token": token, "decision": "approve",
        }).status_code, 302)
        self.assertEqual(self.login(self.client, "newuser", "User-test-password-123").status_code, 302)
        self.assertEqual(self.client.get("/", follow_redirects=True).status_code, 200)
        self.assertEqual(self.client.get("/admin/account-requests").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/account-requests/{user['id']}", data={
            "csrf_token": self.token(self.client, "/account"), "decision": "approve",
        }).status_code, 403)

    def test_rejected_requests_cannot_log_in(self):
        self.request_user()
        self.login(self.client)
        user = self.store.by_username("newuser")
        response = self.client.post(f"/admin/account-requests/{user['id']}", data={
            "csrf_token": self.token(self.client, "/admin/account-requests"), "decision": "reject",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.store.by_username("newuser")["status"], "rejected")
        self.assertEqual(self.login(self.app.test_client(), "newuser", "User-test-password-123").status_code, 401)

    def test_csrf_logout_and_dashboard_api_actions(self):
        self.assertEqual(self.client.post("/login", data={"username": "admin"}).status_code, 400)
        self.token(self.client)
        self.assertEqual(self.client.post("/login", data={"csrf_token": "\u00e9"}).status_code, 400)
        self.assertEqual(self.login(self.client).status_code, 302)
        self.assertEqual(self.client.post("/api/speedtest/run").status_code, 400)
        token = self.token(self.client, "/account")
        response = self.client.post("/api/cell-tags", json={}, headers={"X-CSRF-Token": token})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["message"], "cell_id is required")
        self.assertEqual(self.client.get("/logout").status_code, 405)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": token}).status_code, 302)
        self.assertEqual(self.client.get("/api/routers").status_code, 401)

    def test_password_change_invalidates_other_sessions_and_survives_bootstrap(self):
        other = self.app.test_client()
        self.login(self.client)
        self.login(other)
        response = self.client.post("/account", data={
            "csrf_token": self.token(self.client, "/account"),
            "current_password": "Initial-test-password-123", "password": "Changed-test-password-456",
            "confirm_password": "Changed-test-password-456",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/account").status_code, 200)
        self.assertEqual(other.get("/api/routers").status_code, 401)
        self.store.ready = False
        self.store.initialize()
        self.assertEqual(self.login(other, password="Initial-test-password-123").status_code, 401)
        self.assertEqual(self.login(other, password="Changed-test-password-456").status_code, 302)

    def test_login_limit_persists_between_clients(self):
        for _ in range(10):
            self.assertEqual(self.login(self.client, password="wrong").status_code, 401)
        other = self.app.test_client()
        self.assertEqual(self.login(other).status_code, 429)

    def test_invalid_password_change_leaves_existing_credentials_valid(self):
        self.login(self.client)
        response = self.client.post("/account", data={
            "csrf_token": self.token(self.client, "/account"),
            "current_password": "incorrect", "password": "Changed-test-password-456",
            "confirm_password": "Changed-test-password-456",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.login(self.app.test_client()).status_code, 302)

    def test_duplicate_request_does_not_replace_existing_account(self):
        self.assertEqual(self.request_user("admin").status_code, 302)
        self.assertEqual(self.store.by_username("admin")["status"], "active")
        self.assertEqual(self.login(self.client).status_code, 302)

    def test_secure_cookies_and_missing_signing_key(self):
        from flask import Flask
        from unittest.mock import patch
        with patch.dict(os.environ, {"DASHBOARD_SECRET_KEY": ""}):
            with self.assertRaises(RuntimeError):
                self.auth.init_auth(Flask("missing-key"))
        with patch.dict(os.environ, {"AUTH_COOKIE_SECURE": "true"}):
            secure_app = Flask("secure-cookies", template_folder=str(Path(__file__).parent / "templates"))
            secure_app.add_url_rule("/", endpoint="index", view_func=lambda: "ok")
            self.auth.init_auth(secure_app)
            response = secure_app.test_client().get("/login")
            cookie = response.headers["Set-Cookie"]
            self.assertIn("Secure", cookie)
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Lax", cookie)

    def test_preferences_are_validated_protected_and_saved_per_account(self):
        self.assertEqual(self.client.post("/api/account/preferences", json={"mode": "light", "scheme": "ocean"}).status_code, 401)
        self.login(self.client)
        token = self.token(self.client, "/account/personalization")
        headers = {"X-CSRF-Token": token}
        self.assertEqual(self.client.post("/api/account/preferences", json={"mode": "light", "scheme": "ocean"}).status_code, 400)
        for preferences in [{"mode": "system", "scheme": "ocean"}, {"mode": "dark", "scheme": "invalid"}, {"mode": [], "scheme": {}}, []]:
            self.assertEqual(self.client.post("/api/account/preferences", json=preferences, headers=headers).status_code, 400)
        self.request_user()
        other = self.store.by_username("newuser")
        response = self.client.post("/api/account/preferences", json={"mode": "light", "scheme": "ocean", "account_id": other["id"]}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.by_username("admin")["preferences"], {"mode": "light", "scheme": "ocean"})
        self.assertEqual(self.store.by_username("newuser")["preferences"], {})
        self.store.ready = False
        self.store.initialize()
        self.assertEqual(self.store.by_username("admin")["preferences"]["scheme"], "ocean")
        response = self.client.get("/dashboard")
        self.assertIn(b'"scheme": "ocean"', response.data)
        self.assertIn(b'class="app-account"', response.data)

    def test_personalization_form_saves_all_reference_schemes(self):
        from themes import THEMES
        self.login(self.client)
        token = self.token(self.client, "/account/personalization")
        for theme in THEMES:
            response = self.client.post("/account/personalization", data={
                "csrf_token": token, "mode": "dark", "scheme": theme["id"],
            })
            self.assertEqual(response.status_code, 302)
            self.assertEqual(self.store.by_username("admin")["preferences"]["scheme"], theme["id"])
        response = self.client.get("/account")
        self.assertIn(b'aria-label="Settings"', response.data)
        self.assertIn(b"Personalization", response.data)
        self.assertIn(b"Account Settings", response.data)

    def test_file_recovery_resets_admin_and_revokes_session(self):
        self.login(self.client)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".password") as password_file:
            password_file.write("Recovered-test-password-789\n")
            password_file.flush()
            result = subprocess.run([
                sys.executable, str(Path(__file__).with_name("reset_admin_password.py")),
                "--password-file", password_file.name,
            ], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.client.get("/api/routers").status_code, 401)
        self.assertEqual(self.login(self.client, password="Recovered-test-password-789").status_code, 302)

    def test_authentication_fails_closed_when_database_is_down(self):
        from unittest.mock import patch
        import psycopg2
        with patch.object(self.store, "initialize", side_effect=psycopg2.OperationalError("test outage")):
            response = self.client.get("/dashboard")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b"test outage", response.data)
            self.assertEqual(self.client.get("/health").status_code, 200)


if __name__ == "__main__":
    unittest.main()
