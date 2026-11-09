"""Shared account storage, independent of metrics retention and Timescale setup."""
import os
import re
from contextlib import contextmanager
from threading import Lock

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from werkzeug.security import generate_password_hash
from access import DEFAULT_SITE, ROLES


USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}\Z")


def normalize_username(value):
    return str(value or "").strip().lower()


def validate_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError("Use a password between 12 and 128 characters.")


def read_secret(name):
    path = os.getenv(name + "_FILE")
    if path:
        with open(path, encoding="utf-8") as secret_file:
            return secret_file.read().rstrip("\r\n")
    return os.getenv(name, "")


class AccountStore:
    def __init__(self):
        self.ready = False
        self.lock = Lock()

    @contextmanager
    def transaction(self):
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "timescaledb"),
            port=int(os.getenv("DB_PORT", "5432")),
            dbname=os.getenv("DB_NAME"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            connect_timeout=5,
            options="-c statement_timeout=10000 -c lock_timeout=5000",
        )
        try:
            with conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    yield cur
        finally:
            conn.close()

    def initialize(self):
        if self.ready:
            return
        with self.lock:
            if self.ready:
                return
            with self.transaction() as cur:
                # Serialize schema/bootstrap across dashboard replicas.
                cur.execute("SELECT pg_advisory_xact_lock(734912840)")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS dashboard_accounts (
                        id BIGSERIAL PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        full_name TEXT NOT NULL,
                        email TEXT NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        password_hash TEXT NOT NULL,
                        role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
                        status TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'active', 'rejected')),
                        session_version INTEGER NOT NULL DEFAULT 1,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                        reviewed_at TIMESTAMPTZ,
                        reviewed_by BIGINT REFERENCES dashboard_accounts(id)
                    );
                    CREATE TABLE IF NOT EXISTS dashboard_auth_limits (
                        key TEXT PRIMARY KEY,
                        attempts INTEGER NOT NULL,
                        expires_at TIMESTAMPTZ NOT NULL
                    );
                    ALTER TABLE dashboard_accounts
                        ADD COLUMN IF NOT EXISTS preferences JSONB NOT NULL DEFAULT '{}'::jsonb;
                """)
                cur.execute("""
                    ALTER TABLE dashboard_accounts DROP CONSTRAINT IF EXISTS dashboard_accounts_role_check;
                    ALTER TABLE dashboard_accounts ADD CONSTRAINT dashboard_accounts_role_check
                        CHECK (role IN ('admin', 'user', 'operator', 'viewer'));
                    ALTER TABLE dashboard_accounts ALTER COLUMN role SET DEFAULT 'viewer';
                    CREATE TABLE IF NOT EXISTS dashboard_site_settings (
                        id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id), settings JSONB NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS dashboard_action_events (
                        id BIGSERIAL PRIMARY KEY, actor_id BIGINT REFERENCES dashboard_accounts(id),
                        vendor TEXT NOT NULL, action TEXT NOT NULL, status TEXT NOT NULL,
                        message TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE INDEX IF NOT EXISTS dashboard_action_events_vendor_time_idx
                        ON dashboard_action_events (vendor, created_at DESC);
                """)
                cur.execute("SELECT id FROM dashboard_accounts WHERE role = 'admin' LIMIT 1")
                if not cur.fetchone():
                    username = normalize_username(os.getenv("DASHBOARD_ADMIN_USERNAME", "admin"))
                    password = read_secret("DASHBOARD_ADMIN_PASSWORD")
                    if not USERNAME.fullmatch(username):
                        raise ValueError("DASHBOARD_ADMIN_USERNAME is invalid.")
                    validate_password(password)
                    if password in {"replace-me", "change-this-password", "replace-with-strong-password"}:
                        raise ValueError("Set a real DASHBOARD_ADMIN_PASSWORD in the Secret.")
                    cur.execute("""
                        INSERT INTO dashboard_accounts
                            (username, full_name, email, password_hash, role, status)
                        VALUES (%s, 'Administrator', '', %s, 'admin', 'active')
                    """, (username, generate_password_hash(password)))
            self.ready = True

    def by_username(self, username):
        with self.transaction() as cur:
            cur.execute("SELECT * FROM dashboard_accounts WHERE username = %s", (username,))
            return cur.fetchone()

    def by_id(self, account_id):
        with self.transaction() as cur:
            cur.execute("SELECT * FROM dashboard_accounts WHERE id = %s", (account_id,))
            return cur.fetchone()

    def consume_limit(self, key, maximum, seconds):
        with self.transaction() as cur:
            cur.execute("DELETE FROM dashboard_auth_limits WHERE expires_at <= now()")
            cur.execute("""
                INSERT INTO dashboard_auth_limits (key, attempts, expires_at)
                VALUES (%s, 1, now() + %s * interval '1 second')
                ON CONFLICT (key) DO UPDATE
                SET attempts = dashboard_auth_limits.attempts + 1
                RETURNING attempts
            """, (key, seconds))
            return cur.fetchone()["attempts"] <= maximum

    def request_account(self, username, full_name, email, reason, password):
        with self.transaction() as cur:
            cur.execute("""
                INSERT INTO dashboard_accounts
                    (username, full_name, email, reason, password_hash)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (username) DO NOTHING
            """, (username, full_name, email, reason, generate_password_hash(password)))

    def pending(self):
        with self.transaction() as cur:
            cur.execute("""
                SELECT id, username, full_name, email, reason, created_at
                FROM dashboard_accounts WHERE status = 'pending' ORDER BY created_at
            """)
            return cur.fetchall()

    def save_preferences(self, account_id, preferences):
        with self.transaction() as cur:
            cur.execute("UPDATE dashboard_accounts SET preferences = %s WHERE id = %s",
                        (Json(preferences), account_id))

    def review(self, account_id, reviewer_id, status):
        with self.transaction() as cur:
            cur.execute("""
                UPDATE dashboard_accounts SET status = %s, reviewed_at = now(), reviewed_by = %s
                WHERE id = %s AND status = 'pending' AND role IN ('user', 'viewer')
                RETURNING id
            """, (status, reviewer_id, account_id))
            return cur.fetchone() is not None

    def change_password(self, account_id, version, password):
        with self.transaction() as cur:
            cur.execute("""
                UPDATE dashboard_accounts
                SET password_hash = %s, session_version = session_version + 1
                WHERE id = %s AND session_version = %s AND status = 'active'
                RETURNING session_version
            """, (generate_password_hash(password), account_id, version))
            row = cur.fetchone()
            return row["session_version"] if row else None

    def reset_admin_password(self, username, password):
        validate_password(password)
        with self.transaction() as cur:
            cur.execute("""
                UPDATE dashboard_accounts
                SET password_hash = %s, session_version = session_version + 1
                WHERE username = %s AND role = 'admin' AND status = 'active'
                RETURNING id
            """, (generate_password_hash(password), normalize_username(username)))
            if not cur.fetchone():
                raise ValueError("No active administrator with that username exists.")
            cur.execute("DELETE FROM dashboard_auth_limits")

    def site_settings(self):
        with self.transaction() as cur:
            cur.execute("SELECT settings FROM dashboard_site_settings WHERE id = TRUE")
            row = cur.fetchone()
            return {**DEFAULT_SITE, **(row['settings'] if row else {})}

    def save_site_settings(self, settings):
        with self.transaction() as cur:
            cur.execute("""INSERT INTO dashboard_site_settings (id, settings) VALUES (TRUE, %s)
                ON CONFLICT (id) DO UPDATE SET settings = EXCLUDED.settings""", (Json(settings),))

    def users(self):
        with self.transaction() as cur:
            cur.execute("SELECT id, username, full_name, role, status FROM dashboard_accounts ORDER BY id")
            return cur.fetchall()

    def change_role(self, account_id, role):
        if role not in ROLES:
            raise ValueError('Choose a valid role.')
        with self.transaction() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(734912841)')
            cur.execute('SELECT role FROM dashboard_accounts WHERE id = %s AND status = %s FOR UPDATE',
                        (account_id, 'active'))
            target = cur.fetchone()
            if not target:
                raise ValueError('Choose an active account.')
            if target['role'] == 'admin' and role != 'admin':
                cur.execute("SELECT count(*) AS total FROM dashboard_accounts WHERE role = 'admin' AND status = 'active'")
                if cur.fetchone()['total'] <= 1:
                    raise ValueError('The last active administrator cannot be demoted.')
            cur.execute('UPDATE dashboard_accounts SET role = %s, session_version = session_version + 1 WHERE id = %s',
                        (role, account_id))

    def claim_restart(self, account_id, vendor):
        with self.transaction() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ('cpe-router-restart-' + vendor,))
            cur.execute("""SELECT id FROM dashboard_action_events
                WHERE vendor = %s AND action = 'restart' AND created_at > now() - interval '5 minutes' LIMIT 1""", (vendor,))
            if cur.fetchone():
                raise ValueError('A restart was recently requested for this router. Wait five minutes before retrying.')
            cur.execute("""INSERT INTO dashboard_action_events (actor_id, vendor, action, status, message)
                VALUES (%s, %s, 'restart', 'pending', 'Restart requested') RETURNING id""", (account_id, vendor))
            return cur.fetchone()['id']

    def finish_action(self, action_id, status, message):
        with self.transaction() as cur:
            cur.execute('UPDATE dashboard_action_events SET status = %s, message = %s WHERE id = %s',
                        (status, message, action_id))

    def action_events(self):
        with self.transaction() as cur:
            cur.execute("""SELECT e.id, a.username, e.vendor, e.action, e.status, e.message, e.created_at
                FROM dashboard_action_events e LEFT JOIN dashboard_accounts a ON a.id = e.actor_id
                ORDER BY e.created_at DESC LIMIT 30""")
            return cur.fetchall()
