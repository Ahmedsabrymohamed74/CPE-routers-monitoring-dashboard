"""Focused tests for alert rules, shared event state and custom-role/user administration."""
import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from werkzeug.datastructures import MultiDict
import test_auth
import test_portal
from alerting import default_alert_settings, settings_from_form, signal_alerts, transition_events, PROFILE_FIELDS


class RuleTests(unittest.TestCase):
    def test_all_signal_metrics_have_lower_upper_and_missing_is_ignored(self):
        cfg=default_alert_settings()
        for rule in cfg['metrics'].values():rule.update(enabled=True,low=1,high=3)
        sample={key:0 for key in cfg['metrics']}
        self.assertEqual(len(signal_alerts(sample,cfg)),5)
        self.assertEqual(len(signal_alerts({key:4 for key in sample},cfg)),5)
        self.assertEqual(signal_alerts({key:None for key in sample},cfg),[])
        self.assertEqual(signal_alerts({key:1 for key in sample},cfg),[])

    def test_validation_rejects_nonfinite_reversed_and_empty_enabled_bounds(self):
        for data in [{'rsrp_enabled':'on','rsrp_low':'nan'}, {'rsrp_enabled':'on','rsrp_low':'-80','rsrp_high':'-100'}, {'rssi_enabled':'on'}, {'cqi0_low':'16'}, {'stale_minutes':'0'}, {'profile_enabled':'on'}, {'profile_fields':'fake'}]:
            with self.subTest(data=data),self.assertRaises(ValueError):settings_from_form(MultiDict(data))

    def test_profile_baseline_changes_and_missing_values(self):
        cfg=default_alert_settings();cfg['profile_fields']=list(PROFILE_FIELDS)
        first={'cell_id':'1','pci':'0','enodeb_id':'10','tac':'5'}
        events,state=transition_events(first,cfg,{})
        self.assertEqual(events,[])
        events,state=transition_events({'cell_id':None,'pci':'0'},cfg,state)
        self.assertEqual(events,[])
        events,state=transition_events({'cell_id':'2','pci':'1','enodeb_id':'11','tac':'6'},cfg,state)
        self.assertEqual(len(events),1);self.assertIn('Cell ID: 1 → 2',events[0]['detail']);self.assertIn('eNodeB ID',events[0]['detail'])
        events,_=transition_events({'cell_id':'2','pci':'1','enodeb_id':'11','tac':'6'},cfg,state)
        self.assertEqual(events,[])

    def test_signal_breach_is_deduplicated_and_recovers_only_with_valid_data(self):
        cfg=default_alert_settings()
        events,state=transition_events({'rsrp':-120},cfg,{})
        self.assertEqual(len(events),1)
        events,state=transition_events({'rsrp':None},cfg,state);self.assertEqual(events,[])
        events,state=transition_events({'rsrp':-120},cfg,state);self.assertEqual(events,[])
        events,state=transition_events({'rsrp':-80},cfg,state);self.assertEqual(events[0]['kind'],'recovery')


@unittest.skipUnless(os.getenv('AUTH_TEST_DATABASE')=='1','Requires disposable DB')
class SettingsTests(unittest.TestCase):
    setUpClass=classmethod(test_auth.AuthenticationTests.setUpClass.__func__)
    token=test_auth.AuthenticationTests.token
    login=test_auth.AuthenticationTests.login
    account=test_portal.PortalTests.account

    def setUp(self):
        test_auth.AuthenticationTests.setUp(self)
        with self.store.transaction() as cur:
            cur.execute("DELETE FROM dashboard_roles WHERE builtin=FALSE")
            cur.execute('TRUNCATE dashboard_alert_state,dashboard_alert_events,dashboard_site_settings,dashboard_alert_rules')
        self.store.save_alert_settings(default_alert_settings())
        with self.store.transaction() as cur:
            cur.execute("UPDATE dashboard_alert_settings SET settings=jsonb_set(settings,'{_named_rules_migrated}','true'::jsonb) WHERE id=TRUE")
        self.store.save_alert_rule("Test defaults",["huawei","zte"],default_alert_settings())
        self.login(self.client)
        self.csrf=self.token(self.client,'/settings')
        self.module=__import__('app')

    def test_alert_configuration_saved_separately_and_legacy_settings_migrate(self):
        self.store.save_site_settings({'title':'LAN','landing_page':'home','rsrp_warning':-105,'sinr_warning':8})
        with self.store.transaction() as cur:cur.execute('TRUNCATE dashboard_alert_settings')
        self.store.ready=False;self.store.initialize()
        self.assertEqual(self.store.alert_settings()['metrics']['rsrp']['low'],-105)
        self.assertNotIn('rsrp_warning',self.store.site_settings())
        settings_page=self.client.get('/settings').get_data(as_text=True)
        self.assertNotIn('name="rsrp_warning"',settings_page);self.assertNotIn('name="sinr_warning"',settings_page)
        response=self.client.post('/alerts/configure',data=MultiDict([('csrf_token',self.csrf),('name','Custom signal alert'),('routers','huawei'),('enabled','on'),('rsrq_enabled','on'),('rsrq_low','-18'),('rsrq_high','-3'),('profile_enabled','on'),('profile_fields','pci'),('profile_fields','tac'),('stale_minutes','10')]))
        self.assertEqual(response.status_code,303)
        cfg=self.store.alert_rules()[-1]['settings'];self.assertEqual(cfg['metrics']['rsrq'],{'enabled':True,'low':-18.,'high':-3.})
        self.assertEqual(cfg['profile_fields'],['pci','tac'])
        self.assertEqual(self.client.get('/alerts/configure').status_code,200)

    def test_viewer_cannot_configure_alerts_or_create_users_roles(self):
        viewer,_=self.account('viewer')
        for url in ['/alerts/configure','/settings/users','/settings/roles','/settings/roles/viewer/permissions']:
            self.assertEqual(viewer.post(url,data={'csrf_token':'test-csrf'}).status_code,403,url)
        self.assertEqual(viewer.get('/alerts/configure').status_code,403)
        operator,_=self.account('operator');self.assertEqual(operator.get('/alerts/configure').status_code,200)
        self.assertEqual(self.client.post('/alerts/configure',data={}).status_code,400)

    def test_admin_creates_custom_role_and_user_without_admin_privileges(self):
        response=self.client.post('/settings/roles',data=MultiDict([('csrf_token',self.csrf),('label','Signal Analyst'),('permissions','manage_alerts')]))
        self.assertEqual(response.status_code,303)
        role='signal-analyst';self.assertIn(role,self.store.role_catalog())
        response=self.client.post('/settings/users',data={'csrf_token':self.csrf,'username':'analyst','full_name':'Signal Analyst','email':'','password':'Analyst-test-password-123','confirm_password':'Analyst-test-password-123','role':role})
        self.assertEqual(response.status_code,303)
        user=self.store.by_username('analyst');self.assertEqual(user['status'],'active');self.assertEqual(user['role'],role)
        self.assertNotEqual(user['password_hash'],'Analyst-test-password-123')
        client=self.app.test_client();self.assertEqual(self.login(client,'analyst','Analyst-test-password-123').status_code,302)
        self.assertEqual(client.get('/alerts/configure').status_code,200)
        self.assertIn(b'Signal Analyst',client.get('/home').data)
        self.assertEqual(client.post('/api/actions/restart-router',json={'vendor':'huawei','confirmation':'RESTART'},headers={'X-CSRF-Token':self.token(client,'/account')}).status_code,403)
        self.assertEqual(client.post('/settings/users',data={}).status_code,403)
        self.store.update_role_permissions(role,[])
        self.assertEqual(client.get('/api/routers').status_code,401)

    def test_custom_roles_cannot_gain_admin_rights_or_override_builtins(self):
        for permissions in [['restart_router'],['manage_users'],['manage_site']]:
            with self.assertRaises(ValueError):self.store.create_role('Evil Role',permissions)
        with self.assertRaises(ValueError):self.store.create_role('Admin',[])
        with self.assertRaises(ValueError):self.store.update_role_permissions('operator',[])
        role=self.store.create_role('Network Reader',[])
        with self.assertRaises(ValueError):self.store.create_role('Network Reader',[])
        with self.assertRaises(ValueError):self.store.update_role_permissions(role,['restart_router'])
        self.assertNotIn('restart_router',self.store.role_permissions(role))
        with self.assertRaises(ValueError):self.store.change_role(self.store.by_username('admin')['id'],role)

    def test_shared_state_deduplicates_events_and_survives_reinitialization(self):
        start=datetime.now(timezone.utc)-timedelta(minutes=2)
        sample={'router_vendor':'huawei','rsrp':-120,'cell_id':'1','pci':'0'}
        self.store.evaluate_alert_sample(sample,start)
        self.assertEqual(len(self.store.alert_events()),1)
        from auth_store import AccountStore
        other=AccountStore();other.initialize()
        other.evaluate_alert_sample(sample,start+timedelta(seconds=1))
        self.assertEqual(len(other.alert_events()),1)
        changed={**sample,'cell_id':'2'}
        other.evaluate_alert_sample(changed,start+timedelta(seconds=2))
        self.assertEqual(len(other.alert_events()),2)
        # A late sample from another replica must not change the baseline back.
        self.store.evaluate_alert_sample(sample,start)
        other.evaluate_alert_sample(changed,start+timedelta(seconds=3))
        self.assertEqual(len(other.alert_events()),2)
        self.assertEqual(self.client.get('/alerts').status_code,200)

    def test_create_user_validation_does_not_replace_existing_account(self):
        admin=self.store.by_username('admin')
        with self.assertRaises(ValueError):self.store.create_user('admin','Changed','','Valid-test-password-123','viewer',admin['id'])
        with self.assertRaises(ValueError):self.store.create_user('new-user','New','','short','viewer',admin['id'])
        with self.assertRaises(ValueError):self.store.create_user('new-user','New','','Valid-test-password-123','made-up',admin['id'])
        self.assertEqual(self.store.by_username('admin')['password_hash'],admin['password_hash'])
