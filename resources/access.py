"""Shared roles and navigation settings; legacy users retain operator permissions."""
ROLES = {'viewer': 'Viewer', 'operator': 'Operator', 'admin': 'Admin'}
PERMISSIONS = {
    'viewer': {'view'},
    'operator': {'view', 'edit_profiles', 'run_speedtest'},
    'admin': {'view', 'edit_profiles', 'run_speedtest', 'manage_users', 'manage_site', 'restart_router'},
}
LANDING_PAGES = {'home': 'Home overview', 'dashboard': 'Dashboard', 'alerts': 'Alerts', 'settings': 'Settings', 'actions': 'Actions'}
DEFAULT_SITE = {'title': 'Router Monitor', 'description': 'Your home network, at a glance.',
                'landing_page': 'home', 'rsrp_warning': -110, 'sinr_warning': 5}


def effective_role(role):
    return 'operator' if role == 'user' else role


def allowed(role, permission):
    return permission in PERMISSIONS.get(effective_role(role), set())
