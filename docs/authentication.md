# Dashboard Accounts

The dashboard requires username/password login before displaying metrics or allowing API access.
This is browser form authentication with a signed session cookie, rather than an HTTP Basic browser prompt.
Router login credentials remain separate from dashboard accounts.

## First Deployment

PostgreSQL must be available even if historical metric collection is disabled. Accounts, pending
requests, and login attempt limits are shared in the existing database, in separate ordinary tables:
`dashboard_accounts` and `dashboard_auth_limits`. Metrics retention does not delete accounts.

Before running the new image, add these values to the existing `router-dashboard-secret` Kubernetes
Secret using your local secret YAML (`stringData`). Do not commit the real file:

```yaml
stringData:
  # Keep the existing router/database values in this Secret.
  DASHBOARD_SECRET_KEY: <random value from openssl rand -hex 32>
  DASHBOARD_ADMIN_USERNAME: admin
  DASHBOARD_ADMIN_PASSWORD: <a unique password of 12-128 characters>
```

Generate the session signing key locally:

```bash
openssl rand -hex 32
```

Apply your complete local Secret YAML, then restart the deployment after deploying the new image.
The current Deployment already imports `router-dashboard-secret` with `envFrom`; no manifest
structure changes are needed. All replicas must use the same signing key and database.
The example Secret contains placeholders only and must be populated before use.

The first HTTP request (other than `/health` or static files) bootstraps the `admin` account if no administrator exists.
On subsequent starts, bootstrap values never overwrite the stored password. Changing only
`DASHBOARD_ADMIN_PASSWORD` in the Secret does **not** reset an existing account.
The optional `DASHBOARD_ADMIN_PASSWORD_FILE` and `DASHBOARD_SECRET_KEY_FILE` environment variables
read these values from mounted files instead of environment values; file values take precedence.
An unreadable bootstrap file or invalid initial password causes initialization to fail closed.

Do not put passwords or the session signing key in a ConfigMap. `AUTH_COOKIE_SECURE` is a non-secret
setting and may go in the ConfigMap. It defaults to `true` for the existing HTTPS Traefik ingress.
For an HTTP-only local development session, set `AUTH_COOKIE_SECURE=false` explicitly. Use HTTPS
for deployed access; a secure session cookie is not sent over plain HTTP.

## Account Requests and Approval

1. Open the dashboard URL; unauthenticated visitors see the login page.
2. Select **Request an account** and enter full name, email, username, optional note, and a password.
3. The password is hashed immediately, and the account remains pending.
4. Log in as an administrator, select the user icon, then **Account Requests** in the sidebar.
5. Select **Approve** or **Reject**. Approval activates the requested username and password as a
   Viewer. Only administrators can review requests; the server checks this on every action.

There is no automatic email delivery or email verification. An administrator should verify the
requester's identity before approving. Duplicate usernames do not replace existing accounts.
Rejected requests remain stored and cannot log in or be resubmitted under the same username.

Viewer accounts can read metrics, alerts and action history, and change their own password and appearance.
Operator accounts can also edit cell profiles and run speed tests. Existing legacy `user` accounts
retain Operator permissions. New requests default to Viewer. Admin accounts additionally approve
requests, manage roles/site settings, and restart routers. These checks are enforced by the server.

## Change a Password in the UI

Select your username in the top navigation, choose **Account Settings**, and enter the current password and the new password twice.
Passwords must be 12-128 characters. A successful change revokes other sessions for that account;
the current browser remains signed in. Session cookies expire eight hours after login/password change.
Use **Log out** on the account page to end the current browser session.

## Personalization

Open **Settings → Appearance & colors** for the account area's **Personalization** page.
Its sidebar also contains **Account Settings** (password changes) and, for admins, **Account Requests**.
New browsers default to dark mode with the Violet & Blue scheme. The sun/moon toggle is available
on login, account-request, account, and dashboard pages.

Select appearance and a color scheme, preview the five palette swatches, then select **Save appearance**.
The six schemes are transcribed from the supplied `/opt/THEMES.jpg` reference:
Violet & Blue, Coral & Cobalt, Ocean, Teal & Ember, Sunshine & Lavender, and Mint & Indigo.
Signed-in preferences are saved in PostgreSQL per account and shared across pods/devices.
Anonymous preferences are saved in browser storage. Quick dark/light toggles save immediately
for signed-in users; settings-dropdown changes are saved with **Save appearance**.

## Reset an Admin Password When the UI Is Broken

The recovery command directly updates the existing administrator in PostgreSQL. It does not require
Flask routes, router access, or a browser. PostgreSQL and its existing account tables must be available.
It cannot repair a database outage or create a missing database.

From an application pod, use an interactive prompt so the password is not stored in shell history:

```bash
kubectl -n backend get pods -l app=router-dashboard
kubectl -n backend exec -it <running-dashboard-pod> -- python reset_admin_password.py --username admin
```

If you prefer a local file, create `admin-password.txt` with mode `600`, containing only the new password,
then run this from `resources/` in an environment with the app's `DB_HOST`, `DB_PORT`, `DB_NAME`,
`DB_USER`, and `DB_PASSWORD` configured:

```bash
chmod 600 /secure/path/admin-password.txt
python reset_admin_password.py --username admin --password-file /secure/path/admin-password.txt
```

The file must be owned by the user running the command. Protect/delete it after use. Password files
named `admin-password.txt` or ending in `.password` are ignored by Git. Do not mount a plaintext
password through a ConfigMap. An operator may also mount an owner-only file into a pod for this
explicit command; simply editing a bootstrap password file does not reset existing accounts.

Recovery revokes all sessions for that administrator and clears login attempt limits. No pod restart
is required. Rotating `DASHBOARD_SECRET_KEY` and restarting every replica revokes all browser sessions.

## Operational Notes

- `/health` stays public and returns only `{"status":"ok"}` for existing Kubernetes probes.
- Other pages redirect to login when unauthenticated; dashboard APIs return HTTP 401.
- Missing/failed account storage returns HTTP 503 without allowing unauthenticated dashboard access.
- Login attempts are limited in PostgreSQL per username and per direct client address, across replicas.
  Behind Traefik, the direct address may be the proxy; the shared address limit is deliberately
  conservative. Untrusted forwarded headers are not used to identify a client.
- All form/API mutations require CSRF tokens; the dashboard attaches them to its API requests.
- Passwords use Werkzeug's salted password hashing; no plaintext account passwords are stored in DB.
- Account-service errors are logged in application logs; inspect them with `kubectl logs`.

## Verification

`resources/test_auth.py` is an integration suite for a **disposable** PostgreSQL database. Set
`AUTH_TEST_DATABASE=1`, `DB_NAME=cpe_auth_tests`, and the other database environment variables,
then run from `resources/`:

```bash
python -m unittest -v test_auth
```

The suite truncates the account tables. Never point it at the production database.


## Navigation and landing page

The logo opens `/`, which redirects to the configured landing page. The menu is Dashboard,
Alerts, Settings, Actions. Admins can choose Home, Dashboard, Alerts, Settings or Actions in
**Settings → Site & landing page**, and edit the site name and Home introduction. Settings are
shared across replicas in `dashboard_site_settings`. `/dashboard` retains the charts and filters.
Alerts derive from the latest stored metrics: RSRP/SINR thresholds, samples older than 15 minutes,
and recent failed/uncertain restart requests. They refresh when the page is opened or refreshed.

## Users and router actions

Admins assign Viewer, Operator or Admin under **Settings → Users & roles** after approving a
request. Changing a role revokes that account's existing sessions; the last active admin cannot
be demoted. New roles preserve existing account data and passwords.

**Actions → Restart router** is available only to admins. Choose the router and type `RESTART`
in the confirmation dialog. The server requires an admin session and CSRF token. Commands use
Huawei `device/control` with Control=1, or ZTE `REBOOT_DEVICE` with the current authentication
challenge. Firmware support must be validated on the actual router during an intentional restart.
No factory-reset command is used. Each request is recorded in `dashboard_action_events` and a
PostgreSQL lock/cooldown permits one request per vendor every five minutes across replicas.
A lost response is recorded as `unknown` and is never automatically retried. Check connectivity
before requesting another restart. A confirmed response acknowledges the request; it does not
prove that the router has finished restarting.

Role/action integration tests: `AUTH_TEST_DATABASE=1 DB_NAME=cpe_auth_tests python -m unittest -v test_auth test_portal`.
These tests truncate disposable account tables and mock all restart commands. Never run them on
production. UI tests require jsdom: `NODE_PATH=/path/to/node_modules node scripts/test_portal_ui.cjs`.
