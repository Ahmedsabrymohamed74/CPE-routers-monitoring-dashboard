"""Alert configuration and deterministic sample evaluation (no router/network calls)."""
from copy import deepcopy
from datetime import datetime, timezone
import math
import re
import operator
import hashlib

SIGNAL_METRICS = {
    'rsrp': {'label': 'RSRP', 'unit': 'dBm', 'minimum': -200, 'maximum': 0, 'low': -110},
    'rsrq': {'label': 'RSRQ', 'unit': 'dB', 'minimum': -60, 'maximum': 30, 'low': -15},
    'sinr': {'label': 'SINR', 'unit': 'dB', 'minimum': -50, 'maximum': 100, 'low': 5},
    'rssi': {'label': 'RSSI', 'unit': 'dBm', 'minimum': -200, 'maximum': 0, 'low': -90},
    'cqi0': {'label': 'CQI', 'unit': '', 'minimum': 0, 'maximum': 15, 'low': 5},
}
PROFILE_FIELDS = {
    'cell_id': 'Cell ID', 'pci': 'PCI', 'enodeb_id': 'eNodeB ID', 'tac': 'TAC',
    'plmn': 'PLMN', 'band': 'Band', 'earfcn': 'EARFCN', 'transmode': 'Transmission mode',
    'network_type': 'Network type', 'operator': 'Operator', 'dl_bandwidth': 'Downlink bandwidth',
    'ul_bandwidth': 'Uplink bandwidth', 'custom_label': 'Saved profile label',
    'iso_region_code': 'Saved profile region', 'custom_fields': 'Saved custom profile fields',
}


COMPARISONS = {'<': operator.lt, '<=': operator.le, '==': operator.eq,
               '!=': operator.ne, '>=': operator.ge, '>': operator.gt}
OPERATOR_LABELS = {'<': '<  Less than', '<=': '<=  Less than or equal', '==': '==  Equal',
                   '!=': '!=  Not equal', '>=': '>=  Greater than or equal', '>': '>  Greater than'}


def condition_rows(settings):
    logic = settings.get('signal_logic', 'OR')
    if 'conditions' in settings:
        return [dict(row, join=row.get('join', logic)) for row in settings['conditions']]
    # Preserve old below/above settings as strict comparisons, including disabled rows.
    rows = []
    for key, rule in settings.get('metrics', {}).items():
        if key not in SIGNAL_METRICS:
            continue
        for bound, op in [('low', '<'), ('high', '>')]:
            if rule.get(bound) is not None:
                rows.append({'id': key + ':' + bound, 'metric': key, 'operator': op,
                             'value': rule[bound], 'enabled': rule['enabled'], 'join': logic})
    return rows


def conditions_from_form(form):
    ids = form.getlist('condition_id')
    metrics = form.getlist('condition_metric')
    operators = form.getlist('condition_operator')
    values = form.getlist('condition_value')
    enabled = set(form.getlist('condition_enabled'))
    joins = form.getlist('condition_join')
    if not joins:
        joins = [form.get('signal_logic', 'OR')] * len(ids)
    if len(joins) != len(ids) or any(join not in {'AND', 'OR'} for join in joins):
        raise ValueError('Choose AND or OR between each signal condition.')
    if not len(ids) == len(metrics) == len(operators) == len(values) or len(ids) > 32:
        raise ValueError('Use up to 32 complete signal conditions.')
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r'[A-Za-z0-9:_-]{1,64}', key) for key in ids) or not enabled <= set(ids):
        raise ValueError('Signal condition identifiers are invalid. Reload the editor.')
    result = []
    for key, metric, op, raw, join in zip(ids, metrics, operators, values, joins):
        if metric not in SIGNAL_METRICS or op not in COMPARISONS:
            raise ValueError('Choose a supported signal metric and comparison operator.')
        spec = SIGNAL_METRICS[metric]
        try:
            value = float(raw) if raw.strip() else None
        except (TypeError, ValueError):
            raise ValueError(spec['label'] + ': enter a numeric threshold.')
        if value is None and key in enabled:
            raise ValueError(spec['label'] + ': an enabled condition needs a threshold.')
        if value is not None and (not math.isfinite(value) or not spec['minimum'] <= value <= spec['maximum']):
            raise ValueError(f"{spec['label']}: use a threshold between {spec['minimum']} and {spec['maximum']}.")
        result.append({'id': key, 'metric': metric, 'operator': op, 'value': value, 'enabled': key in enabled, 'join': join})
    return result


def default_alert_settings(legacy=None):
    legacy = legacy or {}
    metrics = {key: {'enabled': key in ('rsrp', 'sinr'), 'low': value['low'], 'high': None}
               for key, value in SIGNAL_METRICS.items()}
    for key in ('rsrp', 'sinr'):
        try:
            value = float(legacy[key + '_warning'])
            if math.isfinite(value):
                metrics[key]['low'] = value
        except (KeyError, TypeError, ValueError):
            pass
    return {'metrics': metrics, 'signal_logic': 'OR', 'profile_enabled': True,
            'profile_fields': ['cell_id', 'pci', 'enodeb_id'],
            'stale_enabled': True, 'stale_minutes': 15, 'action_failures': True}


def normalize_settings(settings):
    result = default_alert_settings()
    if settings:
        result.update({k: v for k, v in settings.items() if k != 'metrics'})
        for key, rule in settings.get('metrics', {}).items():
            if key in result['metrics']:
                result['metrics'][key].update(rule)
    if result.get('signal_logic') not in {'AND', 'OR'}:
        result['signal_logic'] = 'OR'
    result['conditions'] = condition_rows(result)
    return deepcopy(result)


def settings_from_form(form):
    result = default_alert_settings()
    logic = form.get('signal_logic', 'OR')
    if logic not in {'AND', 'OR'}:
        raise ValueError('Choose AND or OR for signal conditions.')
    result['signal_logic'] = logic
    if form.get('conditions_present') == '1':
        result['conditions'] = conditions_from_form(form)
        for rule in result['metrics'].values():
            rule['enabled'] = False
    else:
        for key, spec in SIGNAL_METRICS.items():
            rule = {'enabled': form.get(key + '_enabled') == 'on'}
            for bound in ('low', 'high'):
                raw = form.get(key + '_' + bound, '').strip()
                try:
                    value = float(raw) if raw else None
                except (TypeError, ValueError):
                    raise ValueError(spec['label'] + ': enter numeric bounds or leave them empty.')
                if value is not None and (not math.isfinite(value) or not spec['minimum'] <= value <= spec['maximum']):
                    raise ValueError(f"{spec['label']}: bounds must be between {spec['minimum']} and {spec['maximum']}.")
                rule[bound] = value
            if rule['enabled'] and rule['low'] is None and rule['high'] is None:
                raise ValueError(spec['label'] + ': enable at least one bound.')
            if rule['low'] is not None and rule['high'] is not None and rule['low'] >= rule['high']:
                raise ValueError(spec['label'] + ': lower bound must be less than upper bound.')
            result['metrics'][key] = rule
        result['conditions'] = condition_rows(result)
    fields = form.getlist('profile_fields')
    if any(key not in PROFILE_FIELDS for key in fields):
        raise ValueError('Choose supported profile fields.')
    result['profile_enabled'] = form.get('profile_enabled') == 'on'
    result['profile_fields'] = list(dict.fromkeys(fields))
    if result['profile_enabled'] and not fields:
        raise ValueError('Choose at least one profile field to monitor.')
    try:
        result['stale_minutes'] = int(form.get('stale_minutes', '15'))
    except (ValueError, TypeError):
        raise ValueError('Stale-data timeout must be a whole number of minutes.')
    if not 1 <= result['stale_minutes'] <= 1440:
        raise ValueError('Stale-data timeout must be between 1 and 1440 minutes.')
    result['stale_enabled'] = form.get('stale_enabled') == 'on'
    result['action_failures'] = form.get('action_failures') == 'on'
    return result


def number(value):
    if value is None:
        return None
    match = re.fullmatch(r'\s*(-?\d+(?:\.\d+)?)\s*(?:dBm|dB)?\s*', str(value), re.I)
    if not match:
        return None
    value = float(match.group(1))
    return value if math.isfinite(value) else None


def signal_states(sample, settings):
    states = {}
    for condition in condition_rows(settings):
        if not condition['enabled']:
            continue
        value = number(sample.get(condition['metric']))
        states[condition['id']] = None if value is None else COMPARISONS[condition['operator']](value, condition['value'])
    return states


def signal_groups(settings):
    """AND binds before OR; disabled rows are omitted from the expression."""
    groups = []
    for row in condition_rows(settings):
        if not row['enabled']:
            continue
        if not groups or row['join'] == 'OR':
            groups.append([])
        groups[-1].append(row)
    return groups


def group_key(group, groups, legacy_logic='OR'):
    if len(groups) == 1 and legacy_logic == 'AND':
        return 'signal:all'
    if len(group) == 1:
        return group[0]['id']
    if len(groups) == 1:
        return 'signal:all'  # Preserve state for existing all-AND alerts.
    digest = hashlib.sha256('|'.join(row['id'] for row in group).encode()).hexdigest()[:24]
    return 'signal:group:' + digest


def signal_group_states(sample, settings):
    states = signal_states(sample, settings)
    groups = signal_groups(settings)
    result = {}
    for group in groups:
        values = [states[row['id']] for row in group]
        result[group_key(group, groups, settings.get('signal_logic'))] = False if False in values else (True if all(value is True for value in values) else None)
    return result


def condition_text(row):
    metric = SIGNAL_METRICS[row['metric']]
    return f"{metric['label']} {row['operator']} {row['value']:g} {metric['unit']}".strip()


def signal_expression(settings):
    groups = signal_groups(settings)
    terms = []
    for group in groups:
        text = ' AND '.join(condition_text(row) for row in group)
        terms.append('(' + text + ')' if len(group) > 1 and len(groups) > 1 else text)
    return ' OR '.join(terms)


def signal_alerts(sample, settings):
    states = signal_group_states(sample, settings)
    groups = signal_groups(settings)
    result = []
    for group in groups:
        key = group_key(group, groups, settings.get('signal_logic'))
        if states[key] is not True:
            continue
        parts = []
        for row in group:
            metric = SIGNAL_METRICS[row['metric']]
            parts.append(condition_text(row) + f" (current {number(sample.get(row['metric'])):g} {metric['unit']})")
        result.append({'key': key, 'kind': 'signal', 'severity': 'warning',
                       'title': condition_text(group[0]) if len(group) == 1 else 'Signal conditions matched (AND)',
                       'detail': ' · '.join(parts)})
    return result


def profile_snapshot(sample):
    result = {}
    for key in PROFILE_FIELDS:
        value = (sample.get('cell_profile') or {}).get('custom_fields') if key == 'custom_fields' else sample.get(key)
        if value is not None and value != '' and value != '--':
            result[key] = value if isinstance(value, dict) else str(value).strip()
    return result


def transition_events(sample, settings, previous):
    """Only entering/leaving a breach and real known-to-known profile changes emit events."""
    active = signal_alerts(sample, settings)
    active_keys = {item['key'] for item in active}
    old_active = set(previous.get('active', []))
    events = [dict(item) for item in active if item['key'] not in old_active]
    by_id = {item['id']: item for item in condition_rows(settings)}
    group_states = signal_group_states(sample, settings)
    for key in old_active - active_keys:
        # Unknown values do not falsely clear a matched AND term or individual condition.
        if key in group_states and group_states[key] is None:
            active_keys.add(key)
            continue
        condition = by_id.get(key)
        label = SIGNAL_METRICS[condition['metric']]['label'] if condition else 'Combined signal conditions'
        events.append({'key': key, 'kind': 'recovery', 'severity': 'info', 'title': 'Signal alert cleared',
                       'detail': label + ': no longer matches the configured condition.'})
    old_profile = previous.get('profile', {})
    new_profile = profile_snapshot(sample)
    if settings['profile_enabled']:
        changed = [key for key in settings['profile_fields'] if key in old_profile and key in new_profile
                   and old_profile[key] != new_profile[key]]
        if changed:
            events.append({'key': 'profile', 'kind': 'profile', 'severity': 'warning', 'title': 'Cell/tower profile changed',
                           'detail': ' · '.join(f'{PROFILE_FIELDS[key]}: {old_profile[key]} → {new_profile[key]}' for key in changed)})
    return events, {'active': sorted(active_keys), 'profile': {**old_profile, **new_profile}}


def sample_time(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value is None:
        return datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def new_alert_settings():
    result = default_alert_settings()
    for rule in result['metrics'].values():
        rule['enabled'] = False
    result.update(profile_enabled=False, stale_enabled=False, action_failures=False)
    return result


def rule_summary(settings):
    result = []
    expression = signal_expression(settings)
    if expression:
        result.append('Signal: ' + expression)
    if settings['profile_enabled']:
        result.append('Profile changes: ' + ', '.join(PROFILE_FIELDS[key] for key in settings['profile_fields']))
    if settings['stale_enabled']:
        result.append(f"Metrics stale after {settings['stale_minutes']} minutes")
    if settings['action_failures']:
        result.append('Failed or uncertain router restarts')
    return result
