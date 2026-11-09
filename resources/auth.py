"""Dashboard login, request approval, and session protection."""
import hashlib
import os
import re
import secrets
from datetime import timedelta

import psycopg2
from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from auth_store import AccountStore, USERNAME, normalize_username, read_secret, validate_password
from themes import THEMES, valid_preferences
from access import DEFAULT_SITE, ROLES, effective_role, allowed


auth = Blueprint("auth", __name__)
store = AccountStore()
DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def error_response(message, status):
    if request.path.startswith("/api/"):
        return jsonify(error=True, message=message), status
    return render_template("auth.html", mode="unavailable", error=message), status


def init_auth(app):
    secret_key = read_secret("DASHBOARD_SECRET_KEY")
    if len(secret_key) < 32 or secret_key == "replace-with-random-session-signing-key":
        raise RuntimeError("Set DASHBOARD_SECRET_KEY to a random value of at least 32 characters in the Secret.")
    app.config.update(
        SECRET_KEY=secret_key,
        SESSION_COOKIE_NAME="cpe_dashboard_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=os.getenv("AUTH_COOKIE_SECURE", "true").lower() in {"true", "1", "yes", "on"},
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    app.register_blueprint(auth)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.context_processor
    def theme_context():
        user = getattr(g, "current_user", None)
        preferences = user.get("preferences") if user else None
        return {"theme_options": THEMES, "theme_preferences": preferences if valid_preferences(preferences) else None,
                "site": store.site_settings() if user else DEFAULT_SITE,
                "role_label": ROLES.get(effective_role(user['role']), 'Viewer') if user else '',
                "can": lambda permission: bool(user and allowed(user['role'], permission))}

    @app.before_request
    def protect_dashboard():
        g.current_user = None
        if request.endpoint == "static":
            return None
        if request.endpoint == "health":
            # Kubernetes probes must work without a browser session.
            return None
        try:
            store.initialize()
            account_id = session.get("account_id")
            if account_id is not None:
                account = store.by_id(account_id)
                if account and account["status"] == "active" and account["session_version"] == session.get("version"):
                    g.current_user = account
                else:
                    session.clear()
        except (psycopg2.Error, ValueError, OSError):
            app.logger.exception("Account storage or bootstrap configuration unavailable")
            return error_response("Account service unavailable. Contact the administrator.", 503)

        public = request.endpoint in {"auth.login", "auth.request_account"}
        if not public and g.current_user is None:
            if request.path.startswith("/api/"):
                return error_response("Please log in to continue.", 401)
            return redirect(url_for("auth.login"))
        if request.endpoint in {"auth.admin_requests", "auth.review_request", "portal.set_role",
                                "portal.save_site_settings", "portal.restart_router"}:
            if g.current_user["role"] != "admin":
                return error_response("Administrator access is required.", 403)
        permission = {'save_cell_tag': 'edit_profiles', 'speedtest_run': 'run_speedtest'}.get(request.endpoint)
        if permission and not allowed(g.current_user['role'], permission):
            return error_response('This action requires an Operator or Admin account.', 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not expected or not secrets.compare_digest(expected.encode(), token.encode()):
                return error_response("This form has expired. Refresh the page and try again.", 400)

    @app.after_request
    def protect_responses(response):
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.errorhandler(psycopg2.Error)
    def account_database_error(exc):
        app.logger.exception("Account database operation failed")
        return error_response("Account service unavailable. Try again later.", 503)


def rate_allowed(scope, value, maximum, seconds):
    digest = hashlib.sha256(str(value).encode()).hexdigest()
    return store.consume_limit(scope + ":" + digest, maximum, seconds)


@auth.route("/login", methods=["GET", "POST"])
def login():
    if g.current_user:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = normalize_username(request.form.get("username"))
        password = request.form.get("password", "")
        allowed_ip = rate_allowed("login-ip", request.remote_addr, 30, 900)
        allowed_user = rate_allowed("login-user", username[:64], 10, 900)
        if not (allowed_ip and allowed_user):
            return render_template("auth.html", mode="login", error="Too many login attempts. Try again in 15 minutes."), 429
        account = store.by_username(username) if USERNAME.fullmatch(username) else None
        valid = check_password_hash(account["password_hash"] if account else DUMMY_HASH, password[:129])
        if valid and account and account["status"] == "active":
            session.clear()
            session.permanent = True
            session["account_id"] = account["id"]
            session["version"] = account["session_version"]
            csrf_token()
            return redirect(url_for("index"))
        return render_template("auth.html", mode="login", error="Invalid credentials or account not yet approved."), 401
    return render_template("auth.html", mode="login")


@auth.route("/request-account", methods=["GET", "POST"])
def request_account():
    if request.method == "POST":
        if not rate_allowed("request-ip", request.remote_addr, 5, 3600):
            return render_template("auth.html", mode="request", error="Too many requests. Try again in an hour."), 429
        username = normalize_username(request.form.get("username"))
        full_name = request.form.get("full_name", "").strip()
        email = request.form.get("email", "").strip()
        reason = request.form.get("reason", "").strip()
        password = request.form.get("password", "")
        try:
            if not USERNAME.fullmatch(username):
                raise ValueError("Username must be 3-64 lowercase letters, numbers, dots, hyphens or underscores, starting with a letter or number.")
            if not 1 <= len(full_name) <= 120:
                raise ValueError("Enter your full name (up to 120 characters).")
            if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                raise ValueError("Enter a valid email address.")
            if len(reason) > 1000:
                raise ValueError("Keep the request note under 1,000 characters.")
            validate_password(password)
            if password != request.form.get("confirm_password"):
                raise ValueError("Passwords do not match.")
            store.request_account(username, full_name, email, reason, password)
        except ValueError as exc:
            return render_template("auth.html", mode="request", error=str(exc)), 400
        flash("Request received. An administrator must approve your account before you can log in. Existing usernames are unchanged.")
        return redirect(url_for("auth.login"))
    return render_template("auth.html", mode="request")


@auth.route("/account", methods=["GET", "POST"])
def account():
    if request.method == "POST":
        user = g.current_user
        if not rate_allowed("password-user", user["id"], 10, 900):
            return render_template("auth.html", mode="account", error="Too many attempts. Try again in 15 minutes."), 429
        try:
            if not check_password_hash(user["password_hash"], request.form.get("current_password", "")[:129]):
                raise ValueError("Current password is incorrect.")
            password = request.form.get("password", "")
            validate_password(password)
            if password != request.form.get("confirm_password"):
                raise ValueError("Passwords do not match.")
            if check_password_hash(user["password_hash"], password):
                raise ValueError("Choose a password different from the current password.")
            version = store.change_password(user["id"], user["session_version"], password)
            if version is None:
                session.clear()
                return redirect(url_for("auth.login"))
            session.clear()
            session.permanent = True
            session["account_id"] = user["id"]
            session["version"] = version
            csrf_token()
            flash("Password changed. Other signed-in sessions have been revoked.")
            return redirect(url_for("auth.account"))
        except ValueError as exc:
            return render_template("auth.html", mode="account", error=str(exc)), 400
    return render_template("auth.html", mode="account")


@auth.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@auth.route("/account/personalization", methods=["GET", "POST"])
def personalization():
    if request.method == "POST":
        preferences = {"mode": request.form.get("mode"), "scheme": request.form.get("scheme")}
        if not valid_preferences(preferences):
            return render_template("auth.html", mode="personalization", error="Choose a valid appearance and color scheme."), 400
        store.save_preferences(g.current_user["id"], preferences)
        flash("Appearance saved.")
        return redirect(url_for("auth.personalization"))
    return render_template("auth.html", mode="personalization")


@auth.post("/api/account/preferences")
def save_preferences():
    preferences = request.get_json(silent=True)
    if not valid_preferences(preferences):
        return jsonify(error=True, message="Choose a valid appearance and color scheme."), 400
    preferences = {key: preferences[key] for key in ("mode", "scheme")}
    store.save_preferences(g.current_user["id"], preferences)
    return jsonify(saved=True, preferences=preferences)


@auth.get("/admin/account-requests")
def admin_requests():
    return render_template("auth.html", mode="admin", account_requests=store.pending())


@auth.post("/admin/account-requests/<int:account_id>")
def review_request(account_id):
    decision = request.form.get("decision")
    if decision not in {"approve", "reject"}:
        return error_response("Choose Approve or Reject.", 400)
    updated = store.review(account_id, g.current_user["id"], "active" if decision == "approve" else "rejected")
    flash("Account request " + ("approved." if decision == "approve" else "rejected.") if updated else "This request has already been reviewed.")
    return redirect(url_for("auth.admin_requests"))
