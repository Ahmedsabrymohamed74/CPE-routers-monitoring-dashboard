"""Shared roles and navigation settings; legacy users retain operator permissions."""
ROLES = {'viewer': 'Viewer', 'operator': 'Operator', 'admin': 'Admin'}
PERMISSIONS = {
    'viewer': {'view'},
    'operator': {'view', 'edit_profiles', 'run_speedtest', 'manage_alerts'},
    'admin': {'view', 'edit_profiles', 'run_speedtest', 'manage_users', 'manage_site', 'manage_alerts', 'restart_router'},
}
LANDING_PAGES = {'home': 'Home overview', 'dashboard': 'Dashboard', 'alerts': 'Alerts', 'settings': 'Settings', 'actions': 'Actions'}
DEFAULT_SITE = {'title': 'Router Monitor', 'description': 'Your home network, at a glance.',
                'landing_page': 'home'}


def effective_role(role):
    return 'operator' if role == 'user' else role


def allowed(role, permission):
    return permission in PERMISSIONS.get(effective_role(role), set())

CUSTOM_PERMISSIONS = {'edit_profiles': 'Edit cell profiles', 'run_speedtest': 'Run speed tests', 'manage_alerts': 'Configure alerts'}
