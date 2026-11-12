"""Menu pages and admin-only router actions. Reads use stored metrics, not router commands."""
from datetime import datetime, timezone

import requests
from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for, abort
from access import LANDING_PAGES, CUSTOM_PERMISSIONS, effective_role
from alerting import SIGNAL_METRICS, PROFILE_FIELDS, signal_alerts, settings_from_form, sample_time, new_alert_settings, rule_summary, condition_rows, OPERATOR_LABELS
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
        rules = store.alert_rules()
        cards = {card['vendor']: card for card in router_cards()}
        actions = store.action_events()
        items = []
        for rule in rules:
            settings = rule['settings']
            rule['summary'] = rule_summary(settings)
            statuses = []
            for vendor in rule['routers']:
                card = cards.get(vendor)
                label = card['name'] if card else vendor.upper()
                condition_count = 0
                status = 'Disabled'
                sample = card['latest'] if card else None
                if rule['enabled']:
                    if not card or not card['configured']:
                        status = 'Router not configured'
                    elif not sample:
                        status = 'Waiting for samples'
                    elif (datetime.now(timezone.utc) - sample_time(sample['time'])).total_seconds() > settings['stale_minutes'] * 60:
                        status = 'Stale samples'
                        if settings['stale_enabled']:
                            items.append({'severity': 'warning', 'title': rule['name'] + ' · Metrics are stale',
                                          'detail': f"{label} · older than {settings['stale_minutes']} minutes", 'time': sample['time']})
                            condition_count += 1
                    else:
                        status = 'Monitoring'
                        for item in signal_alerts(sample, settings):
                            items.append({**item, 'title': rule['name'] + ' · ' + item['title'],
                                          'detail': label + ' · ' + item['detail'], 'time': sample['time']})
                            condition_count += 1
                    if settings['action_failures']:
                        for event in actions:
                            if event['vendor'] == vendor and event['status'] in ('failed', 'unknown'):
                                items.append({'severity': 'warning', 'title': rule['name'] + ' · Router restart ' + event['status'],
                                              'detail': label + ' · ' + event['message'], 'time': event['created_at'].isoformat()})
                                condition_count += 1
                    if condition_count:
                        status = f'Triggered ({condition_count})'
                details = {'Monitoring': 'Fresh samples are available; watching the enabled conditions.',
                           'Stale samples': f"Latest sample is older than {settings['stale_minutes']} minutes. This is sample freshness, not an online/offline check.",
                           'Waiting for samples': 'No recorded sample is available for this router.',
                           'Router not configured': 'No router credentials are configured.', 'Disabled': 'This alert is disabled.'}
                statuses.append({'name': label, 'status': status, 'triggered': condition_count > 0,
                                 'detail': details.get(status, 'The current sample matches alert conditions.'),
                                 'last_sample': sample['time'] if sample else None})
            rule['router_statuses'] = statuses
        return render_template('portal.html', page='alerts', alerts=items, rules=rules, events=store.alert_events())

    @portal.get('/alerts/notifications')
    def notifications():
        return render_template('portal.html', page='notifications')

    def alert_form(rule=None, status=200):
        settings = rule['settings'] if rule else new_alert_settings()
        form_rule = dict(rule) if rule else {'id': None, 'name': '', 'enabled': True,
            'routers': [vendor for vendor, router in routers.items()
                        if getattr(router, 'password', None) or getattr(router, 'session_cookie', None)]}
        if rule:
            settings['conditions'] = condition_rows(settings)
        else:
            settings['conditions'] = [{'id': 'condition-1', 'metric': 'rsrp', 'operator': '<', 'value': -110, 'enabled': True, 'join': 'OR'}]
        if request.method == 'POST':
            # Keep submitted input visible after validation errors; never silently discard it.
            form_rule.update(name=request.form.get('name', ''), routers=request.form.getlist('routers'),
                             enabled=request.form.get('enabled') == 'on')
            settings = {**settings, 'metrics': {key: {'enabled': request.form.get(key + '_enabled') == 'on',
                        'low': request.form.get(key + '_low', ''), 'high': request.form.get(key + '_high', '')}
                        for key in SIGNAL_METRICS}}
            settings['signal_logic'] = request.form.get('signal_logic', 'OR')
            if request.form.get('conditions_present') == '1':
                ids = request.form.getlist('condition_id')
                enabled = set(request.form.getlist('condition_enabled'))
                ids = request.form.getlist('condition_id')
                metrics = request.form.getlist('condition_metric')
                ops = request.form.getlist('condition_operator')
                values = request.form.getlist('condition_value')
                joins = request.form.getlist('condition_join')
                settings['conditions'] = [{'id': key, 'metric': metric, 'operator': op, 'value': value,
                    'enabled': key in enabled, 'join': joins[index] if index < len(joins) and joins[index] in {'AND','OR'} else settings['signal_logic']}
                    for index, (key, metric, op, value) in enumerate(zip(ids, metrics, ops, values))]
            else:
                settings.pop('conditions', None)
                settings['conditions'] = condition_rows(settings)
            settings.update(profile_enabled=request.form.get('profile_enabled') == 'on',
                            profile_fields=request.form.getlist('profile_fields'),
                            stale_enabled=request.form.get('stale_enabled') == 'on',
                            stale_minutes=request.form.get('stale_minutes', '15'),
                            action_failures=request.form.get('action_failures') == 'on')
        return render_template('portal.html', page='alert_rules', alert_rule=form_rule, alert_settings=settings,
                               router_choices=routers, signal_metrics=SIGNAL_METRICS, profile_fields=PROFILE_FIELDS, operators=OPERATOR_LABELS), status

    @portal.get('/alerts/configure')
    def configure_alerts():
        return alert_form()

    @portal.post('/alerts/configure')
    def save_alerts():
        try:
            rule_id = store.save_alert_rule(request.form.get('name'), request.form.getlist('routers'),
                        settings_from_form(request.form), request.form.get('enabled') == 'on')
        except ValueError as exc:
            flash(str(exc))
            return alert_form(status=400)
        flash('Alert saved. It is listed below; events appear when its conditions trigger.')
        return redirect(url_for('portal.alerts', _anchor='rule-' + rule_id)), 303

    @portal.get('/alerts/<rule_id>/edit')
    def edit_alert(rule_id):
        rule = store.alert_rule(rule_id)
        if not rule:
            abort(404)
        return alert_form(rule)

    @portal.post('/alerts/<rule_id>/edit')
    def update_alert(rule_id):
        rule = store.alert_rule(rule_id)
        if not rule:
            abort(404)
        try:
            store.save_alert_rule(request.form.get('name'), request.form.getlist('routers'),
                    settings_from_form(request.form), request.form.get('enabled') == 'on', rule_id=rule_id)
        except ValueError as exc:
            flash(str(exc))
            return alert_form(rule, status=400)
        flash('Alert updated.')
        return redirect(url_for('portal.alerts', _anchor='rule-' + rule_id)), 303

    @portal.post('/alerts/<rule_id>/delete')
    def delete_alert(rule_id):
        if not store.delete_alert_rule(rule_id):
            abort(404)
        flash('Alert deleted. Its recorded events are kept.')
        return redirect(url_for('portal.alerts')), 303

    @portal.get('/settings')
    def settings():
        users = store.users() if g.current_user['role'] == 'admin' else []
        return render_template('portal.html', page='settings', users=users,
                               roles={key: value['label'] for key, value in g.role_catalog.items()},
                               role_rows=list(g.role_catalog.values()), custom_permissions=CUSTOM_PERMISSIONS,
                               effective_role=effective_role, landing_pages=LANDING_PAGES)

    @portal.post('/settings/users')
    def create_user():
        try:
            if request.form.get('password') != request.form.get('confirm_password'):
                raise ValueError('Passwords do not match.')
            store.create_user(request.form.get('username'), request.form.get('full_name'),
                              request.form.get('email'), request.form.get('password'),
                              request.form.get('role'), g.current_user['id'])
            flash('User created and ready to sign in.')
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for('portal.settings', _anchor='users')), 303

    @portal.post('/settings/roles')
    def create_role():
        try:
            store.create_role(request.form.get('label'), request.form.getlist('permissions'))
            flash('Role created. You can now assign it to users.')
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for('portal.settings', _anchor='roles')), 303

    @portal.post('/settings/roles/<role>/permissions')
    def update_role(role):
        try:
            store.update_role_permissions(role, request.form.getlist('permissions'))
            flash('Role permissions saved. Assigned users must sign in again.')
        except ValueError as exc:
            flash(str(exc))
        return redirect(url_for('portal.settings', _anchor='roles')), 303

    @portal.post('/settings/site')
    def save_site_settings():
        try:
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            landing = request.form.get('landing_page')
            if not 1 <= len(title) <= 60 or len(description) > 240 or landing not in LANDING_PAGES:
                raise ValueError('Enter a title up to 60 characters, a description up to 240 characters and a valid landing page.')
            store.save_site_settings({'title': title, 'description': description, 'landing_page': landing})
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
