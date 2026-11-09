"""Menu pages and admin-only router actions. Reads use stored metrics, not router commands."""
from datetime import datetime, timezone

import requests
from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for
from access import DEFAULT_SITE, LANDING_PAGES, ROLES, effective_role
from auth import store

portal = Blueprint('portal', __name__)


def init_portal(app, routers, history_reader, ensure_history, router_lock):
    def recent(vendor, **kwargs):
        if not history_reader:
            return []
        ensure_history()
        return history_reader(router_vendor=vendor, **kwargs)

    def router_cards():
        cards = []
        for vendor, router in routers.items():
            configured = bool(getattr(router, 'password', None) or getattr(router, 'session_cookie', None))
            items = recent(vendor, latest=True, limit=1) if configured else []
            cards.append({'vendor': vendor, 'name': router.router_name, 'configured': configured,
                          'latest': items[0] if items else None})
        return cards

    @portal.get('/home')
    def home():
        cards = router_cards()
        return render_template('portal.html', page='home', cards=cards)

    @portal.get('/alerts')
    def alerts():
        settings = store.site_settings()
        items = []
        for card in router_cards():
            sample = card['latest']
            if not sample:
                if card['configured']:
                    items.append({'severity': 'info', 'title': 'No recorded metrics', 'detail': card['name'], 'time': None})
                continue
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(sample['time'])).total_seconds()
            if age > 900:
                items.append({'severity': 'warning', 'title': 'Metrics are older than 15 minutes',
                              'detail': card['name'], 'time': sample['time']})
                continue
            for key, threshold, unit in [('rsrp', settings['rsrp_warning'], 'dBm'), ('sinr', settings['sinr_warning'], 'dB')]:
                if sample.get(key) is not None and float(sample[key]) < threshold:
                    items.append({'severity': 'warning', 'title': f'{key.upper()} below threshold',
                                  'detail': f"{card['name']} · {sample[key]} {unit} (threshold {threshold}) · Cell {sample.get('cell_id') or 'unknown'}",
                                  'time': sample['time']})
        for event in store.action_events():
            if event['status'] in ('failed', 'unknown'):
                items.append({'severity': 'warning', 'title': 'Router restart ' + event['status'],
                              'detail': event['vendor'].upper() + ' · ' + event['message'],
                              'time': event['created_at'].isoformat()})
        return render_template('portal.html', page='alerts', alerts=items)

    @portal.get('/settings')
    def settings():
        users = store.users() if g.current_user['role'] == 'admin' else []
        return render_template('portal.html', page='settings', users=users, roles=ROLES,
                               effective_role=effective_role, landing_pages=LANDING_PAGES)

    @portal.post('/settings/site')
    def save_site_settings():
        try:
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            landing = request.form.get('landing_page')
            rsrp = float(request.form.get('rsrp_warning', '-110'))
            sinr = float(request.form.get('sinr_warning', '5'))
            if not 1 <= len(title) <= 60 or len(description) > 240 or landing not in LANDING_PAGES:
                raise ValueError('Enter a title up to 60 characters, a description up to 240 characters and a valid landing page.')
            if not -160 <= rsrp <= -40 or not -30 <= sinr <= 50:
                raise ValueError('Use an RSRP threshold from -160 to -40 and a SINR threshold from -30 to 50.')
            store.save_site_settings({'title': title, 'description': description, 'landing_page': landing,
                                      'rsrp_warning': rsrp, 'sinr_warning': sinr})
        except (ValueError, TypeError) as exc:
            flash(str(exc))
            return redirect(url_for('portal.settings')), 303
        flash('Site settings saved.')
        return redirect(url_for('portal.settings')), 303

    @portal.post('/settings/users/<int:account_id>/role')
    def set_role(account_id):
        try:
            store.change_role(account_id, request.form.get('role'))
            flash('Role updated. The account must sign in again.')
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for('portal.settings')), 303

    @portal.get('/actions')
    def actions():
        return render_template('portal.html', page='actions', routers=routers, events=store.action_events())

    @portal.post('/api/actions/restart-router')
    def restart_router():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or payload.get('confirmation') != 'RESTART':
            return jsonify(error=True, message='Type RESTART to confirm.'), 400
        vendor = payload.get('vendor')
        if not isinstance(vendor, str) or vendor not in routers:
            return jsonify(error=True, message='Choose a supported router.'), 400
        router = routers[vendor]
        if not (getattr(router, 'password', None) or getattr(router, 'session_cookie', None)):
            return jsonify(error=True, message='This router is not configured.'), 400
        try:
            action_id = store.claim_restart(g.current_user['id'], vendor)
        except ValueError as exc:
            return jsonify(error=True, message=str(exc)), 429
        status, message = 'confirmed', 'Router confirmed the restart request. Connectivity may take a few minutes to return.'
        try:
            with router_lock:
                router.restart()
        except requests.RequestException:
            status, message = 'unknown', 'The router did not confirm before the connection ended. Check its status before retrying.'
        except Exception as exc:
            app.logger.warning('Router restart rejected: vendor=%s event=%s exception=%s', vendor, action_id, type(exc).__name__)
            status, message = 'failed', 'The router rejected the restart request. Check router configuration and firmware support.'
        store.finish_action(action_id, status, message)
        return jsonify(status=status, message=message, event_id=action_id), (502 if status == 'failed' else 202)

    app.register_blueprint(portal)
