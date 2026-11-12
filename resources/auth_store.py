"""Shared account storage, independent of metrics retention and Timescale setup."""
import os
import re
import uuid
from contextlib import contextmanager
from threading import Lock

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from werkzeug.security import generate_password_hash
from access import DEFAULT_SITE, ROLES, PERMISSIONS, CUSTOM_PERMISSIONS, effective_role
from alerting import default_alert_settings, normalize_settings, transition_events, sample_time, condition_rows


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
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS dashboard_roles (
                        key TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE,
                        permissions JSONB NOT NULL, builtin BOOLEAN NOT NULL DEFAULT FALSE
                    );
                    CREATE TABLE IF NOT EXISTS dashboard_alert_settings (
                        id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id), settings JSONB NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS dashboard_alert_state (
                        vendor TEXT PRIMARY KEY, observed_at TIMESTAMPTZ NOT NULL, state JSONB NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS dashboard_alert_events (
                        id BIGSERIAL PRIMARY KEY, vendor TEXT NOT NULL, kind TEXT NOT NULL,
                        key TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL,
                        detail TEXT NOT NULL, observed_at TIMESTAMPTZ NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS dashboard_alert_events_time_idx
                        ON dashboard_alert_events (observed_at DESC);
                    CREATE TABLE IF NOT EXISTS dashboard_alert_rules (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, routers JSONB NOT NULL,
                        settings JSONB NOT NULL, enabled BOOLEAN NOT NULL DEFAULT TRUE,
                        revision INTEGER NOT NULL DEFAULT 1,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    ALTER TABLE dashboard_alert_events ADD COLUMN IF NOT EXISTS rule_id TEXT;
                    ALTER TABLE dashboard_alert_events ADD COLUMN IF NOT EXISTS rule_name TEXT;
                """)
                for key, label in {**ROLES, 'user': 'Legacy operator'}.items():
                    cur.execute("INSERT INTO dashboard_roles (key,label,permissions,builtin) VALUES (%s,%s,%s,TRUE) ON CONFLICT DO NOTHING",
                                (key, label, Json(sorted(PERMISSIONS[effective_role(key)]))))
                cur.execute("""DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'dashboard_accounts_role_fk'
                                   AND conrelid = 'dashboard_accounts'::regclass) THEN
                        ALTER TABLE dashboard_accounts ADD CONSTRAINT dashboard_accounts_role_fk
                            FOREIGN KEY (role) REFERENCES dashboard_roles(key);
                    END IF;
                END $$;""")
                cur.execute('SELECT settings FROM dashboard_site_settings WHERE id = TRUE')
                legacy = cur.fetchone()
                cur.execute("INSERT INTO dashboard_alert_settings(id,settings) VALUES (TRUE,%s) ON CONFLICT DO NOTHING",
                            (Json(default_alert_settings(legacy['settings'] if legacy else None)),))
                cur.execute("UPDATE dashboard_site_settings SET settings = settings - 'rsrp_warning' - 'sinr_warning' WHERE id = TRUE")
                # Copy the latest single configuration exactly once; deleting all rules must stay empty.
                cur.execute("""INSERT INTO dashboard_alert_rules(id,name,routers,settings)
                    SELECT 'legacy', 'Existing alert rules', '["huawei","zte"]'::jsonb,
                           settings - '_named_rules_migrated'
                    FROM dashboard_alert_settings WHERE id=TRUE AND NOT (settings ? '_named_rules_migrated')
                    ON CONFLICT(id) DO NOTHING""")
                cur.execute("""UPDATE dashboard_alert_settings SET settings=
                    jsonb_set(settings,'{_named_rules_migrated}','true'::jsonb) WHERE id=TRUE""")
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
            return {key: (row['settings'] if row else {}).get(key, value) for key, value in DEFAULT_SITE.items()}

    def save_site_settings(self, settings):
        with self.transaction() as cur:
            cur.execute("""INSERT INTO dashboard_site_settings (id, settings) VALUES (TRUE, %s)
                ON CONFLICT (id) DO UPDATE SET settings = EXCLUDED.settings""", (Json(settings),))

    def users(self):
        with self.transaction() as cur:
            cur.execute("SELECT id, username, full_name, role, status FROM dashboard_accounts ORDER BY id")
            return cur.fetchall()

    def change_role(self, account_id, role):
        if role not in self.role_catalog():
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

    def role_catalog(self):
        with self.transaction() as cur:
            cur.execute("SELECT key,label,permissions,builtin FROM dashboard_roles WHERE key <> 'user' ORDER BY builtin DESC,label")
            return {row['key']: dict(row) for row in cur.fetchall()}

    def role_permissions(self, role, catalog=None):
        role = effective_role(role)
        if role in PERMISSIONS:
            return set(PERMISSIONS[role])
        row = (catalog if catalog is not None else self.role_catalog()).get(role)
        return {'view'} | (set(row['permissions']) & set(CUSTOM_PERMISSIONS)) if row else set()

    def create_role(self, label, permissions):
        label = str(label or '').strip()
        key = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')
        if not 2 <= len(label) <= 40 or not re.fullmatch(r'[a-z][a-z0-9-]{1,31}', key):
            raise ValueError('Use a role name of 2–40 characters starting with a letter (maximum 32 letters/numbers in its key).')
        if key in {*ROLES, 'user'} or any(value not in CUSTOM_PERMISSIONS for value in permissions):
            raise ValueError('Choose a new role name and supported permissions. Administrator privileges are reserved.')
        try:
            with self.transaction() as cur:
                cur.execute('INSERT INTO dashboard_roles(key,label,permissions) VALUES (%s,%s,%s)',
                            (key, label, Json(sorted({'view', *permissions}))))
        except psycopg2.errors.UniqueViolation:
            raise ValueError('A role with that name already exists.')
        return key

    def update_role_permissions(self, key, permissions):
        if any(value not in CUSTOM_PERMISSIONS for value in permissions):
            raise ValueError('Administrator privileges cannot be assigned to custom roles.')
        with self.transaction() as cur:
            cur.execute('UPDATE dashboard_roles SET permissions=%s WHERE key=%s AND builtin=FALSE RETURNING key',
                        (Json(sorted({'view', *permissions})), key))
            if not cur.fetchone():
                raise ValueError('Only custom roles can be edited.')
            cur.execute('UPDATE dashboard_accounts SET session_version=session_version+1 WHERE role=%s', (key,))

    def create_user(self, username, full_name, email, password, role, actor_id):
        username = normalize_username(username)
        full_name = str(full_name or '').strip()
        email = str(email or '').strip()
        if not USERNAME.fullmatch(username) or not 1 <= len(full_name) <= 120:
            raise ValueError('Enter a valid username and a name up to 120 characters.')
        if len(email) > 254 or (email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)):
            raise ValueError('Enter a valid email or leave it empty.')
        validate_password(password)
        if role not in self.role_catalog():
            raise ValueError('Choose a valid role.')
        password_hash = generate_password_hash(password)
        try:
            with self.transaction() as cur:
                cur.execute("""INSERT INTO dashboard_accounts
                    (username,full_name,email,password_hash,role,status,reviewed_by,reviewed_at)
                    VALUES (%s,%s,%s,%s,%s,'active',%s,now())""",
                            (username, full_name, email, password_hash, role, actor_id))
        except psycopg2.errors.UniqueViolation:
            raise ValueError('That username is already in use.')

    def alert_settings(self):
        with self.transaction() as cur:
            cur.execute('SELECT settings FROM dashboard_alert_settings WHERE id = TRUE')
            row = cur.fetchone()
            return normalize_settings(row['settings'] if row else None)

    def save_alert_settings(self, settings):
        with self.transaction() as cur:
            cur.execute("""INSERT INTO dashboard_alert_settings(id,settings) VALUES (TRUE,%s)
                ON CONFLICT(id) DO UPDATE SET settings=EXCLUDED.settings""", (Json(settings),))

    def alert_rules(self):
        with self.transaction() as cur:
            cur.execute('SELECT * FROM dashboard_alert_rules ORDER BY created_at,id')
            return [dict(row, settings=normalize_settings(row['settings'])) for row in cur.fetchall()]

    def alert_rule(self, rule_id):
        with self.transaction() as cur:
            cur.execute('SELECT * FROM dashboard_alert_rules WHERE id=%s', (rule_id,))
            row = cur.fetchone()
            return dict(row, settings=normalize_settings(row['settings'])) if row else None

    def save_alert_rule(self, name, routers, settings, enabled=True, rule_id=None):
        name = str(name or '').strip()
        if not 1 <= len(name) <= 80:
            raise ValueError('Enter an alert name of 1–80 characters.')
        if not routers or any(value not in {'huawei', 'zte'} for value in routers):
            raise ValueError('Select at least one supported router.')
        routers = list(dict.fromkeys(routers))
        if enabled and not (any(condition['enabled'] for condition in condition_rows(settings)) or
                            settings['profile_enabled'] or settings['stale_enabled'] or settings['action_failures']):
            raise ValueError('Enable at least one signal, profile, stale-data or router-action condition.')
        with self.transaction() as cur:
            if rule_id:
                cur.execute("""UPDATE dashboard_alert_rules SET name=%s,routers=%s,settings=%s,enabled=%s,
                    revision=revision+1,updated_at=now() WHERE id=%s RETURNING id""",
                            (name, Json(routers), Json(settings), enabled, rule_id))
                if not cur.fetchone():
                    raise ValueError('This alert no longer exists.')
            else:
                rule_id = uuid.uuid4().hex
                cur.execute('INSERT INTO dashboard_alert_rules(id,name,routers,settings,enabled) VALUES (%s,%s,%s,%s,%s)',
                            (rule_id, name, Json(routers), Json(settings), enabled))
        return rule_id

    def delete_alert_rule(self, rule_id):
        with self.transaction() as cur:
            cur.execute('DELETE FROM dashboard_alert_rules WHERE id=%s RETURNING id', (rule_id,))
            return cur.fetchone() is not None

    def evaluate_alert_sample(self, sample, observed_at=None):
        self.initialize()
        vendor = sample['router_vendor']
        observed_at = sample_time(observed_at or sample.get('time'))
        with self.transaction() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('cpe-alert-evaluation-' + vendor,))
            cur.execute('SELECT * FROM dashboard_alert_rules WHERE enabled=TRUE AND routers @> %s::jsonb', (Json([vendor]),))
            rules = cur.fetchall()
            cur.execute('SELECT observed_at,state FROM dashboard_alert_state WHERE vendor=%s', (vendor,))
            previous = cur.fetchone()
            if previous and previous['observed_at'] >= observed_at:
                return
            previous_state = previous['state'] if previous else {}
            next_rules = {}
            for rule in rules:
                prior = previous_state.get('rules', {}).get(rule['id'], {})
                if not prior and rule['id'] == 'legacy' and 'rules' not in previous_state:
                    prior = previous_state
                if prior.get('revision', rule['revision']) != rule['revision']:
                    prior = {'profile': prior.get('profile', {})}
                events, state = transition_events(sample, normalize_settings(rule['settings']), prior)
                state['revision'] = rule['revision']
                next_rules[rule['id']] = state
                for event in events:
                    cur.execute("""INSERT INTO dashboard_alert_events
                        (vendor,kind,key,severity,title,detail,observed_at,rule_id,rule_name)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                (vendor, event['kind'], event['key'], event['severity'], event['title'], event['detail'],
                                 observed_at, rule['id'], rule['name']))
            cur.execute("""INSERT INTO dashboard_alert_state(vendor,observed_at,state) VALUES (%s,%s,%s)
                ON CONFLICT(vendor) DO UPDATE SET observed_at=EXCLUDED.observed_at,state=EXCLUDED.state""",
                        (vendor, observed_at, Json({'rules': next_rules})))
            cur.execute("DELETE FROM dashboard_alert_events WHERE observed_at < now() - interval '30 days'")

    def alert_events(self):
        with self.transaction() as cur:
            cur.execute('SELECT * FROM dashboard_alert_events ORDER BY observed_at DESC,id DESC LIMIT 100')
            return cur.fetchall()
