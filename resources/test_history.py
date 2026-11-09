"""Integration tests for issues #25-27; use only a disposable PostgreSQL database.

Run with HISTORY_TEST_DATABASE=1 and DB_NAME=cpe_issues_tests. Never use production.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone


@unittest.skipUnless(os.getenv('HISTORY_TEST_DATABASE') == '1', 'Requires an isolated history test database')
class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.getenv('DB_NAME') != 'cpe_issues_tests':
            raise RuntimeError('History tests require the disposable cpe_issues_tests database')
        os.environ.update(DASHBOARD_SECRET_KEY='history-test-signing-key-at-least-32-characters',
                          DASHBOARD_ADMIN_PASSWORD='History-test-password-123',
                          AUTH_COOKIE_SECURE='false', HUAWEI_ROUTER_PASSWORD='test-only')
        import db
        import app
        cls.db = db
        cls.web = app
        db.init_db()
        app.ensure_db_initialized()
        cls.client = app.app.test_client()
        # Account support may be an independent local change; test both app configurations.
        cls.auth_enabled = 'auth' in app.app.blueprints
        if cls.auth_enabled:
            cls.client.get('/login')
            with cls.client.session_transaction() as session:
                token = session['csrf_token']
            response = cls.client.post('/login', data={'csrf_token': token, 'username': 'admin',
                                                       'password': 'History-test-password-123'})
            if response.status_code != 302:
                raise RuntimeError('Test admin login failed')

    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.old = self.now - timedelta(days=3)
        rows = [
            (self.old, 'huawei', '100', '0', '10', '20', -90),
            (self.now - timedelta(minutes=5), 'huawei', '100', '0', '10', '20', -80),
            (self.now - timedelta(minutes=4), 'huawei', '100', '1', '10', '20', -100),
            (self.now - timedelta(minutes=3), 'huawei', '100', '0', '11', '21', -70),
            (self.now - timedelta(minutes=2), 'huawei', '200', None, None, None, -110),
            (self.now - timedelta(minutes=1), 'huawei', '200', '', '', '', -109),
            (self.now, 'zte', '100', '0', '10', '20', -120),
        ]
        with self.db.get_conn() as conn, conn.cursor() as cur:
            cur.execute('TRUNCATE cellular_metrics, cell_id_tags')
            cur.executemany('''INSERT INTO cellular_metrics
                (time,router_vendor,cell_id,pci,enodeb_id,tac,rsrp) VALUES (%s,%s,%s,%s,%s,%s,%s)''', rows)
        self.db.upsert_cell_tag('100', '0', 'EG-C', custom_label='<Tower & coast>',
                                router_vendor='huawei', profile_enodeb_id='10', profile_tac='20')

    def test_all_sectors_and_exact_pci_enodeb_tac_selection(self):
        rows = self.db.fetch_history(minutes=60, cell_id='100', router_vendor='huawei')
        self.assertEqual(len(rows), 3)
        sector = self.db.fetch_history(minutes=60, cell_id='100', router_vendor='huawei',
                                      pci='0', enodeb_id='10', tac='20')
        self.assertEqual([row['rsrp'] for row in sector], [-80])
        self.assertEqual(sector[0]['custom_label'], '<Tower & coast>')
        changed_tower = self.db.fetch_history(minutes=60, cell_id='100', router_vendor='huawei',
                                             pci='0', enodeb_id='11', tac='21')
        self.assertEqual([row['rsrp'] for row in changed_tower], [-70])

    def test_groups_include_tower_identity_and_all_time_first_seen(self):
        groups = self.db.fetch_history_cell_groups(minutes=60, router_vendor='huawei')
        self.assertEqual(len(groups), 4)
        sector = next(g for g in groups if g['cell_id'] == '100' and g['enodeb_id'] == '10' and g['pci'] == '0')
        self.assertEqual(sector['tac'], '20')
        self.assertEqual(sector['profile_enodeb_id'], '10')
        self.assertEqual(sector['sample_count'], 1)
        self.assertEqual(sector['avg_rsrp'], -80)
        self.assertEqual(datetime.fromisoformat(sector['first_seen']), self.old)

    def test_missing_metadata_is_one_group_and_can_be_selected(self):
        groups = self.db.fetch_history_cell_groups(minutes=60, router_vendor='huawei')
        missing = [g for g in groups if g['cell_id'] == '200']
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]['sample_count'], 2)
        rows = self.db.fetch_history(minutes=60, router_vendor='huawei', cell_id='200',
                                     pci='', enodeb_id='', tac='')
        self.assertEqual(len(rows), 2)

    def test_vendor_scoping_and_parameterized_filters(self):
        rows = self.db.fetch_history(minutes=60, cell_id='100', router_vendor='zte', pci='0')
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]['custom_label'])
        self.assertEqual(self.db.fetch_history(minutes=60, router_vendor='huawei', pci="0' OR TRUE --"), [])

    def test_latest_and_custom_range_apply_sector_filters(self):
        rows = self.db.fetch_history(latest=True, limit=1, cell_id='100', router_vendor='huawei',
                                     pci='0', enodeb_id='10', tac='20')
        self.assertEqual([r['rsrp'] for r in rows], [-80])
        rows = self.db.fetch_history(start=(self.old-timedelta(seconds=1)).isoformat(),
                                     end=(self.old+timedelta(seconds=1)).isoformat(),
                                     cell_id='100', router_vendor='huawei', pci='0', enodeb_id='10', tac='20')
        self.assertEqual([r['rsrp'] for r in rows], [-90])

    def test_history_api_passes_sector_filters_and_keeps_all_group_choices(self):
        response = self.client.get('/api/history?vendor=huawei&minutes=60&cell_id=100&pci=0&enodeb_id=10&tac=20')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual([r['rsrp'] for r in payload['items']], [-80])
        self.assertEqual(len(payload['cell_groups']), 4)
        self.assertEqual(payload['pci'], '0')
        self.assertEqual(payload['enodeb_id'], '10')
        self.assertEqual(payload['tac'], '20')

    def test_dashboard_renders_and_history_respects_configured_auth(self):
        response = self.client.get('/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="historyProfileFilter"', response.data)
        self.assertIn(b'id="historyLoading"', response.data)
        self.assertEqual(self.web.app.test_client().get('/api/history').status_code,
                         401 if self.auth_enabled else 200)


if __name__ == '__main__':
    unittest.main()
