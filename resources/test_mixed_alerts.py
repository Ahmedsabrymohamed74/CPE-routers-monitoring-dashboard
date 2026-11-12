"""Focused per-condition AND/OR expression and preservation tests."""
import os
import itertools
import unittest
from datetime import datetime,timezone,timedelta
from werkzeug.datastructures import MultiDict
import test_named_alerts
from alerting import new_alert_settings,normalize_settings,signal_alerts,signal_expression,settings_from_form,transition_events


def config(joins=('OR','AND','OR')):
    cfg=new_alert_settings()
    cfg['conditions']=[{'id':key,'metric':key,'operator':op,'value':value,'enabled':True,'join':join}
        for key,op,value,join in zip(['rsrp','rsrq','sinr'],['<=','<','<='],[-100,-12,4],joins)]
    return cfg


def sample(a,b,c):
    return {'rsrp':-110 if a else -90,'rsrq':-20 if b else -5,'sinr':2 if c else 10}


def form():
    data=MultiDict([('name','Mixed expression'),('enabled','on'),('routers','huawei'),('conditions_present','1'),('stale_minutes','15')])
    for row in config()['conditions']:
        for field,key in [('condition_id','id'),('condition_metric','metric'),('condition_operator','operator'),('condition_value','value'),('condition_join','join')]:data.add(field,str(row[key]))
        data.add('condition_enabled',row['id'])
    return data


class ExpressionTests(unittest.TestCase):
    def test_and_then_or_matches_users_example_for_all_truth_values(self):
        for a,b,c in itertools.product([False,True],repeat=3):
            with self.subTest(a=a,b=b,c=c):self.assertEqual(bool(signal_alerts(sample(a,b,c),config())),(a and b) or c)
        self.assertEqual(signal_expression(config()),'(RSRP <= -100 dBm AND RSRQ < -12 dB) OR SINR <= 4 dB')

    def test_and_has_precedence_when_or_is_first(self):
        cfg=config(('OR','OR','AND'))
        for a,b,c in itertools.product([False,True],repeat=3):self.assertEqual(bool(signal_alerts(sample(a,b,c),cfg)),a or (b and c))

    def test_disabled_row_is_omitted_and_missing_values_do_not_false_clear(self):
        cfg=config();cfg['conditions'][1]['enabled']=False
        self.assertTrue(signal_alerts(sample(True,False,False),cfg))
        cfg=config();events,state=transition_events(sample(True,True,False),cfg,{})
        self.assertEqual(len(events),1)
        events,state=transition_events({'rsrp':None,'rsrq':-20,'sinr':10},cfg,state);self.assertEqual(events,[])
        events,state=transition_events(sample(False,True,False),cfg,state);self.assertEqual(events[0]['kind'],'recovery')

    def test_existing_global_logic_is_translated_without_changing_behavior(self):
        for logic in ['AND','OR']:
            cfg=config();cfg['signal_logic']=logic
            for row in cfg['conditions']:row.pop('join')
            normalized=normalize_settings(cfg)
            self.assertTrue(all(row['join']==logic for row in normalized['conditions']))
            for a,b,c in itertools.product([False,True],repeat=3):self.assertEqual(bool(signal_alerts(sample(a,b,c),normalized)),all([a,b,c]) if logic=='AND' else any([a,b,c]))

    def test_existing_single_condition_and_alert_keeps_its_event_state(self):
        cfg=config();cfg['signal_logic']='AND';cfg['conditions']=cfg['conditions'][:1]
        cfg['conditions'][0].pop('join')
        events,state=transition_events(sample(True,False,False),cfg,{'active':['signal:all'],'profile':{}})
        self.assertEqual(events,[]);self.assertEqual(state['active'],['signal:all'])

    def test_parser_validates_each_connector(self):
        cfg=settings_from_form(form());self.assertEqual([r['join'] for r in cfg['conditions']],['OR','AND','OR'])
        bad=form();bad.setlist('condition_join',['OR','XOR','OR'])
        with self.assertRaises(ValueError):settings_from_form(bad)
        bad=form();bad.setlist('condition_join',['AND'])
        with self.assertRaises(ValueError):settings_from_form(bad)


@unittest.skipUnless(os.getenv('AUTH_TEST_DATABASE')=='1','Requires disposable DB')
class ExpressionIntegrationTests(unittest.TestCase):
    setUpClass=classmethod(test_named_alerts.NamedAlertTests.setUpClass.__func__)
    setUp=test_named_alerts.NamedAlertTests.setUp
    token=test_named_alerts.NamedAlertTests.token
    login=test_named_alerts.NamedAlertTests.login

    def test_mixed_expression_round_trip_and_removed_hints(self):
        data=form();data['csrf_token']=self.csrf
        self.assertEqual(self.client.post('/alerts/configure',data=data).status_code,303)
        rule=self.store.alert_rules()[0]
        self.assertEqual([r['join'] for r in rule['settings']['conditions']],['OR','AND','OR'])
        html=self.client.get('/alerts/'+rule['id']+'/edit').get_data(as_text=True)
        self.assertEqual(html.count('name="condition_join"'),4) # three rows plus add-row template
        self.assertNotIn('name="signal_logic"',html)
        self.assertNotIn('Conditions are compared on the same',html)
        self.assertNotIn('You can use the same metric more than',html)
        self.assertIn('value="AND" selected',html)
        import db
        db.insert_metric({'router_vendor':'huawei',**sample(False,True,True)})
        self.assertIn(b'Triggered conditions (1)',self.client.get('/alerts').data)

    def test_background_uses_the_same_mixed_expression(self):
        key=self.store.save_alert_rule('Mixed',['huawei'],config())
        start=datetime.now(timezone.utc)-timedelta(minutes=1)
        for sec,values in enumerate([(True,False,False),(False,True,True),(False,True,True),(True,True,False)]):
            self.store.evaluate_alert_sample({'router_vendor':'huawei',**sample(*values)},start+timedelta(seconds=sec))
        events=self.store.alert_events();signals=[e for e in events if e['kind']=='signal']
        self.assertEqual(len(signals),2);self.assertTrue(all(e['rule_id']==key for e in events))
