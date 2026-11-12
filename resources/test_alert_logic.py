"""Focused explicit comparison and AND/OR matching tests; integration DB is disposable."""
import os
import unittest
from datetime import datetime,timezone,timedelta
from werkzeug.datastructures import MultiDict
import test_named_alerts
from alerting import new_alert_settings, normalize_settings, settings_from_form, signal_alerts, transition_events, condition_rows


def condition(key,metric,op,value,enabled=True):
    return {'id':key,'metric':metric,'operator':op,'value':value,'enabled':enabled}


def config(logic='OR',rows=None):
    cfg=new_alert_settings();cfg.update(signal_logic=logic,conditions=rows or [condition('rsrp','rsrp','<',-90),condition('rsrq','rsrq','<',-15)])
    return cfg


def form(logic='AND',op='<='):
    return MultiDict([('name','Both signals'),('enabled','on'),('routers','huawei'),('conditions_present','1'),('signal_logic',logic),('stale_minutes','15'),
                     ('condition_id','first'),('condition_enabled','first'),('condition_metric','rsrp'),('condition_operator',op),('condition_value','-90'),
                     ('condition_id','second'),('condition_enabled','second'),('condition_metric','rsrq'),('condition_operator','<'),('condition_value','-15')])


class ComparisonTests(unittest.TestCase):
    def test_all_six_operators_at_below_and_above_threshold(self):
        expected={'<':[True,False,False],'<=':[True,True,False],'==':[False,True,False], '!=':[True,False,True],'>=':[False,True,True],'>':[False,False,True]}
        for op,values in expected.items():
            cfg=config(rows=[condition('one','rsrp',op,-90)])
            for actual,result in zip([-91,-90,-89],values):
                with self.subTest(op=op,value=actual):self.assertEqual(bool(signal_alerts({'rsrp':actual},cfg)),result)
            self.assertEqual(signal_alerts({'rsrp':None},cfg),[])

    def test_and_requires_every_enabled_condition_or_requires_any(self):
        sample={'rsrp':-100,'rsrq':-10}
        self.assertEqual(len(signal_alerts(sample,config('OR'))),1)
        self.assertEqual(signal_alerts(sample,config('AND')),[])
        both=signal_alerts({'rsrp':-100,'rsrq':-20},config('AND'))
        self.assertEqual(len(both),1);self.assertEqual(both[0]['key'],'signal:all')
        cfg=config('AND');cfg['conditions'][1]['enabled']=False
        self.assertEqual(len(signal_alerts(sample,cfg)),1)

    def test_two_conditions_on_same_metric_can_express_inclusive_range(self):
        cfg=config('AND',[condition('min','rsrp','>=',-100),condition('max','rsrp','<=',-80)])
        for value in [-100,-90,-80]:self.assertEqual(len(signal_alerts({'rsrp':value},cfg)),1)
        for value in [-101,-79]:self.assertEqual(signal_alerts({'rsrp':value},cfg),[])

    def test_missing_data_does_not_falsely_clear_an_and_group(self):
        cfg=config('AND')
        events,state=transition_events({'rsrp':-100,'rsrq':-20},cfg,{})
        self.assertEqual(len(events),1)
        events,state=transition_events({'rsrp':None,'rsrq':-20},cfg,state);self.assertEqual(events,[])
        events,state=transition_events({'rsrp':None,'rsrq':-10},cfg,state);self.assertEqual(events[0]['kind'],'recovery')

    def test_form_parser_validates_operators_logic_and_condition_identifiers(self):
        parsed=settings_from_form(form());self.assertEqual(parsed['signal_logic'],'AND')
        self.assertEqual(parsed['conditions'][0]['operator'],'<=')
        for field,value in [('condition_operator','XOR'),('signal_logic','XOR'),('condition_value','NaN'),('condition_metric','fake')]:
            data=form();data[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):settings_from_form(data)
        data=form();data.setlist('condition_id',['same','same'])
        with self.assertRaises(ValueError):settings_from_form(data)

    def test_legacy_bounds_keep_exclusive_comparisons_and_disabled_state(self):
        cfg=new_alert_settings();cfg['metrics']['rsrp'].update(enabled=True,low=-90,high=-60)
        normalized=normalize_settings(cfg)
        self.assertEqual(normalized['signal_logic'],'OR')
        rows={c['id']:c for c in condition_rows(normalized)}
        self.assertEqual(rows['rsrp:low']['operator'],'<');self.assertEqual(rows['rsrp:high']['operator'],'>')
        self.assertFalse(rows['rsrq:low']['enabled'])
        self.assertEqual(signal_alerts({'rsrp':-90},normalized),[])


@unittest.skipUnless(os.getenv('AUTH_TEST_DATABASE')=='1','Requires disposable DB')
class LogicIntegrationTests(unittest.TestCase):
    setUpClass=classmethod(test_named_alerts.NamedAlertTests.setUpClass.__func__)
    setUp=test_named_alerts.NamedAlertTests.setUp
    token=test_named_alerts.NamedAlertTests.token
    login=test_named_alerts.NamedAlertTests.login

    def test_saved_logic_and_operators_render_and_gate_current_conditions(self):
        data=form();data['csrf_token']=self.csrf
        self.assertEqual(self.client.post('/alerts/configure',data=data).status_code,303)
        rule=self.store.alert_rules()[0]
        self.assertEqual(rule['settings']['signal_logic'],'AND')
        self.assertEqual(rule['settings']['conditions'][0]['operator'],'<=')
        edit=self.client.get('/alerts/'+rule['id']+'/edit').get_data(as_text=True)
        self.assertIn('value="AND" selected',edit);self.assertIn('name="condition_operator"',edit)
        self.assertIn('value="&lt;=" selected',edit)
        import db
        db.insert_metric({'router_vendor':'huawei','rsrp':-90,'rsrq':-10})
        self.assertIn(b'Triggered conditions (0)',self.client.get('/alerts').data)
        db.insert_metric({'router_vendor':'huawei','rsrp':-90,'rsrq':-20})
        self.assertIn(b'Triggered conditions (1)',self.client.get('/alerts').data)

    def test_and_background_event_matches_ui_and_recovery_deduplicates(self):
        key=self.store.save_alert_rule('Combined',['huawei'],config('AND'))
        start=datetime.now(timezone.utc)-timedelta(minutes=1)
        for seconds,sample in enumerate([{'rsrp':-100,'rsrq':-10},{'rsrp':-100,'rsrq':-20},{'rsrp':-100,'rsrq':-20},{'rsrp':-80,'rsrq':-20}]):
            self.store.evaluate_alert_sample({'router_vendor':'huawei',**sample},start+timedelta(seconds=seconds))
        events=self.store.alert_events();self.assertEqual(len(events),2)
        self.assertEqual({e['kind'] for e in events},{'signal','recovery'})
        self.assertTrue(all(e['rule_id']==key for e in events))

    def test_rule_edit_changes_logic_without_affecting_router_selection(self):
        key=self.store.save_alert_rule('Original',['huawei','zte'],config('OR'))
        data=form('AND','>=');data['csrf_token']=self.csrf;data.setlist('routers',['huawei','zte'])
        self.assertEqual(self.client.post('/alerts/'+key+'/edit',data=data).status_code,303)
        saved=self.store.alert_rule(key)
        self.assertEqual(saved['routers'],['huawei','zte']);self.assertEqual(saved['settings']['signal_logic'],'AND')
        self.assertEqual(saved['settings']['conditions'][0]['operator'],'>=')
