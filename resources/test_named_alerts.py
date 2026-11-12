"""Focused named-alert CRUD, router scoping, saved-rule visibility and migration tests."""
import os
import unittest
from datetime import datetime,timezone,timedelta
from werkzeug.datastructures import MultiDict
import test_auth
import test_portal
from alerting import new_alert_settings


@unittest.skipUnless(os.getenv('AUTH_TEST_DATABASE')=='1','Requires disposable DB')
class NamedAlertTests(unittest.TestCase):
    setUpClass=classmethod(test_auth.AuthenticationTests.setUpClass.__func__)
    token=test_auth.AuthenticationTests.token
    login=test_auth.AuthenticationTests.login
    account=test_portal.PortalTests.account

    def setUp(self):
        test_auth.AuthenticationTests.setUp(self)
        with self.store.transaction() as cur:
            cur.execute('TRUNCATE dashboard_alert_rules,dashboard_alert_state,dashboard_alert_events')
        self.module=__import__('app')
        self.module.ensure_db_initialized()
        with self.store.transaction() as cur:cur.execute('TRUNCATE cellular_metrics')
        self.login(self.client)
        self.csrf=self.token(self.client,'/settings')

    def form(self,name='Weak Huawei',routers=('huawei',),low='-90',**updates):
        items=[('csrf_token',self.csrf),('name',name),('enabled','on'),('rsrp_enabled','on'),('rsrp_low',low),('stale_minutes','15')]
        items.extend(('routers',vendor) for vendor in routers)
        form=MultiDict(items)
        for key,value in updates.items():form[key]=value
        return form

    def config(self,low=-90):
        cfg=new_alert_settings();cfg['metrics']['rsrp'].update(enabled=True,low=low)
        return cfg

    def test_multiple_alerts_saved_and_visible_even_without_trigger(self):
        for name,routers,low in [('Weak Huawei',('huawei',),'-90'),('Critical ZTE',('zte',),'-110')]:
            response=self.client.post('/alerts/configure',data=self.form(name,routers,low))
            self.assertEqual(response.status_code,303)
            self.assertIn('/alerts#rule-',response.location)
        rules=self.store.alert_rules();self.assertEqual(len(rules),2)
        self.assertEqual(rules[0]['routers'],['huawei']);self.assertEqual(rules[1]['routers'],['zte'])
        import db
        db.insert_metric({'router_vendor':'huawei','rsrp':-79,'cell_id':'1'})
        page=self.client.get('/alerts').get_data(as_text=True)
        self.assertIn('Saved alerts (2)',page);self.assertIn('Weak Huawei',page);self.assertIn('Critical ZTE',page)
        self.assertIn('Triggered conditions (0)',page);self.assertIn('Monitoring',page)
        self.assertEqual(len(self.store.alert_events()),0)
        edit=self.client.get('/alerts/'+rules[1]['id']+'/edit').get_data(as_text=True)
        self.assertIn('value="zte" checked',edit);self.assertIn('value="-110.0"',edit)
        self.assertNotIn('value="huawei" checked',edit)

    def test_router_selection_and_validation_errors_are_visible_without_losing_input(self):
        response=self.client.post('/alerts/configure',data=self.form(name='Keep my input',routers=()))
        self.assertEqual(response.status_code,400)
        self.assertIn(b'Select at least one supported router',response.data)
        self.assertIn(b'value="Keep my input"',response.data)
        self.assertEqual(self.store.alert_rules(),[])
        self.assertEqual(self.client.post('/alerts/configure',data=self.form(routers=('fake',))).status_code,400)
        self.assertEqual(self.client.post('/alerts/configure',data=self.form(low='nan')).status_code,400)
        self.assertEqual(self.client.post('/alerts/configure',data=self.form(name='')).status_code,400)
        page=self.client.get('/alerts/configure').get_data(as_text=True)
        self.assertIn('type="checkbox" name="routers" value="huawei"',page)
        self.assertIn('type="checkbox" name="routers" value="zte"',page)

    def test_rules_evaluate_independently_only_for_selected_routers(self):
        first=self.store.save_alert_rule('Huawei warning',['huawei'],self.config(-90))
        second=self.store.save_alert_rule('Huawei critical',['huawei'],self.config(-110))
        third=self.store.save_alert_rule('ZTE warning',['zte'],self.config(-90))
        start=datetime.now(timezone.utc)-timedelta(minutes=1)
        sample={'router_vendor':'huawei','rsrp':-100,'cell_id':'1'}
        self.store.evaluate_alert_sample(sample,start)
        events=self.store.alert_events();self.assertEqual(len(events),1)
        self.assertEqual((events[0]['rule_id'],events[0]['rule_name']),(first,'Huawei warning'))
        self.store.evaluate_alert_sample(sample,start+timedelta(seconds=1))
        self.assertEqual(len(self.store.alert_events()),1)
        self.store.evaluate_alert_sample({**sample,'rsrp':-120},start+timedelta(seconds=2))
        self.assertEqual({e['rule_id'] for e in self.store.alert_events()},{first,second})
        self.store.evaluate_alert_sample({'router_vendor':'zte','rsrp':-100},start+timedelta(seconds=3))
        self.assertEqual({e['rule_id'] for e in self.store.alert_events()},{first,second,third})

    def test_edit_disable_and_delete_one_alert_does_not_overwrite_another(self):
        first=self.store.save_alert_rule('First',['huawei'],self.config())
        second=self.store.save_alert_rule('Second',['zte'],self.config(-110))
        form=self.form('Changed',('zte',),'-95');form.pop('enabled')
        self.assertEqual(self.client.post('/alerts/'+first+'/edit',data=form).status_code,303)
        changed=self.store.alert_rule(first)
        self.assertEqual((changed['name'],changed['routers'],changed['enabled']),('Changed',['zte'],False))
        self.assertEqual(self.store.alert_rule(second)['settings']['metrics']['rsrp']['low'],-110)
        self.store.evaluate_alert_sample({'router_vendor':'zte','rsrp':-100})
        self.assertEqual(self.store.alert_events(),[])
        self.assertIn(b'Disabled',self.client.get('/alerts').data)
        self.assertEqual(self.client.post('/alerts/'+first+'/delete',data={'csrf_token':self.csrf}).status_code,303)
        self.assertIsNone(self.store.alert_rule(first));self.assertIsNotNone(self.store.alert_rule(second))

    def test_migration_preserves_latest_single_config_and_does_not_resurrect_deleted_rules(self):
        cfg=self.config(-87);cfg['metrics']['sinr'].update(enabled=True,low=2)
        self.store.save_alert_settings(cfg)
        self.store.ready=False;self.store.initialize()
        rules=self.store.alert_rules();self.assertEqual(len(rules),1)
        self.assertEqual(rules[0]['name'],'Existing alert rules')
        self.assertEqual(rules[0]['settings']['metrics']['rsrp']['low'],-87)
        self.assertEqual(rules[0]['routers'],['huawei','zte'])
        self.store.delete_alert_rule(rules[0]['id'])
        self.store.ready=False;self.store.initialize()
        self.assertEqual(self.store.alert_rules(),[])

    def test_viewer_and_csrf_protect_all_named_rule_mutations(self):
        key=self.store.save_alert_rule('Protected',['huawei'],self.config())
        viewer,_=self.account('viewer')
        for path in ['/alerts/configure','/alerts/'+key+'/edit','/alerts/'+key+'/delete']:
            self.assertEqual(viewer.post(path,data={'csrf_token':'test-csrf'}).status_code,403)
            self.assertEqual(self.client.post(path,data={}).status_code,400)
        self.assertEqual(viewer.get('/alerts/'+key+'/edit').status_code,403)
        page=viewer.get('/alerts').get_data(as_text=True)
        self.assertIn('Protected',page);self.assertNotIn('>Edit</a>',page)

    def test_rule_revision_and_retained_history(self):
        key=self.store.save_alert_rule('Original name',['huawei'],self.config())
        start=datetime.now(timezone.utc)-timedelta(minutes=1)
        sample={'router_vendor':'huawei','rsrp':-100}
        self.store.evaluate_alert_sample(sample,start)
        self.store.save_alert_rule('New name',['huawei'],self.config(-95),rule_id=key)
        self.store.evaluate_alert_sample(sample,start+timedelta(seconds=1))
        self.assertEqual({e['rule_name'] for e in self.store.alert_events()},{'Original name','New name'})
        self.store.delete_alert_rule(key)
        self.assertEqual(len(self.store.alert_events()),2)
        self.assertEqual(self.client.get('/alerts/'+key+'/edit').status_code,404)
