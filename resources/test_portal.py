"""Role, navigation, audit, and mocked reboot integration tests; disposable DB only."""
import os
import unittest
from unittest.mock import Mock, patch
import requests
import test_auth


@unittest.skipUnless(os.getenv('AUTH_TEST_DATABASE') == '1', 'Requires disposable database')
class PortalTests(unittest.TestCase):
    setUpClass = classmethod(test_auth.AuthenticationTests.setUpClass.__func__)
    token = test_auth.AuthenticationTests.token
    login = test_auth.AuthenticationTests.login
    request_user = test_auth.AuthenticationTests.request_user

    def setUp(self):
        test_auth.AuthenticationTests.setUp(self)
        with self.store.transaction() as cur:
            cur.execute('TRUNCATE dashboard_site_settings')
        import app
        self.module = app
        self.router = app.routers['huawei']
        self.restart = patch.object(self.router, 'restart').start()
        self.addCleanup(patch.stopall)
        self.login(self.client)
        self.headers = {'X-CSRF-Token': self.token(self.client, '/account')}

    def account(self, role):
        with self.store.transaction() as cur:
            cur.execute("INSERT INTO dashboard_accounts(username,full_name,email,password_hash,role,status) VALUES (%s,%s,'test@example.test','unused',%s,'active') RETURNING id,session_version", (role,role,role))
            account = cur.fetchone()
        client = self.app.test_client()
        with client.session_transaction() as session:
            session.update(account_id=account['id'], version=account['session_version'], csrf_token='test-csrf')
        return client, account['id']

    def post_restart(self, client=None, **overrides):
        payload = {'vendor':'huawei','confirmation':'RESTART'}
        payload.update(overrides)
        return (client or self.client).post('/api/actions/restart-router', json=payload,
            headers=self.headers if client is None else {'X-CSRF-Token':'test-csrf'})

    def test_pages_render_and_menu_order_and_logo_landing(self):
        for url in ['/home','/dashboard','/alerts','/settings','/actions','/account','/account/personalization','/admin/account-requests']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, (url,response.data[:1000]))
            text = response.get_data(as_text=True)
            nav = text[text.index('<nav class="app-nav"'):text.index('</nav>')]
            positions = [nav.index('>'+label+'</a>') for label in ['Dashboard','Alerts','Settings','Actions']]
            self.assertEqual(positions, sorted(positions))
            self.assertIn('href="/"', nav)
        self.assertEqual(self.client.get('/').location, '/home')

    def test_landing_and_brand_settings_persist_and_reject_external_url(self):
        data = dict(csrf_token=self.headers['X-CSRF-Token'],title='My LAN',description='Home network',landing_page='alerts',rsrp_warning='-105',sinr_warning='8')
        self.assertEqual(self.client.post('/settings/site',data=data).status_code,303)
        self.store.ready=False
        self.store.initialize()
        self.assertEqual(self.client.get('/').location,'/alerts')
        self.assertIn(b'My LAN',self.client.get('/home').data)
        data['landing_page']='https://evil.example/'
        self.client.post('/settings/site',data=data)
        self.assertEqual(self.client.get('/').location,'/alerts')

    def test_non_admin_cannot_restart_or_manage_site_or_users(self):
        for role in ['viewer','operator','user']:
            client, account_id = self.account(role)
            self.assertEqual(self.post_restart(client).status_code,403)
            self.assertNotIn(b'id="restartRouter"', client.get('/actions').data)
            self.assertEqual(client.post('/settings/site',data={'csrf_token':'test-csrf'}).status_code,403)
            self.assertEqual(client.post(f'/settings/users/{account_id}/role',data={'csrf_token':'test-csrf','role':'admin'}).status_code,403)
        self.restart.assert_not_called()

    def test_viewer_cannot_change_profiles_or_run_speedtests(self):
        viewer,_=self.account('viewer')
        for url in ['/api/cell-tags','/api/speedtest/run']:
            self.assertEqual(viewer.post(url,json={},headers={'X-CSRF-Token':'test-csrf'}).status_code,403)
        for role in ['operator','user']:
            operator,_=self.account(role)
            response=operator.post('/api/cell-tags',json={},headers={'X-CSRF-Token':'test-csrf'})
            self.assertEqual(response.status_code,400)
            self.assertEqual(response.json['message'],'cell_id is required')

    def test_restart_requires_login_csrf_confirmation_and_valid_router(self):
        self.assertEqual(self.app.test_client().post('/api/actions/restart-router').status_code,401)
        self.assertEqual(self.client.post('/api/actions/restart-router',json={'vendor':'huawei','confirmation':'RESTART'}).status_code,400)
        for payload in [{'confirmation':'yes'}, {'vendor':'invalid'},{'vendor':['huawei']}]:
            response=self.post_restart(**payload)
            self.assertEqual(response.status_code,400)
        self.restart.assert_not_called()

    def test_restart_is_audited_and_shared_cooldown_blocks_second_replica(self):
        response=self.post_restart()
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json['status'],'confirmed')
        self.restart.assert_called_once_with()
        event=self.store.action_events()[0]
        self.assertEqual((event['username'],event['vendor'],event['status']),('admin','huawei','confirmed'))
        from auth_store import AccountStore
        other=AccountStore();other.initialize()
        with self.assertRaises(ValueError): other.claim_restart(self.store.by_username('admin')['id'],'huawei')
        self.assertEqual(self.post_restart().status_code,429)
        self.restart.assert_called_once()

    def test_uncertain_timeout_is_not_retried_and_remains_audited(self):
        self.restart.side_effect=requests.Timeout('test timeout')
        response=self.post_restart()
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json['status'],'unknown')
        self.assertEqual(self.post_restart().status_code,429)
        self.restart.assert_called_once()
        self.assertEqual(self.store.action_events()[0]['status'],'unknown')

    def test_failed_restart_is_safe_and_recorded(self):
        self.restart.side_effect=RuntimeError('secret router response')
        response=self.post_restart()
        self.assertEqual(response.status_code,502)
        self.assertNotIn(b'secret router response',response.data)
        self.assertEqual(self.store.action_events()[0]['status'],'failed')

    def test_last_admin_cannot_be_demoted_and_role_changes_revoke_sessions(self):
        admin=self.store.by_username('admin')
        with self.assertRaises(ValueError): self.store.change_role(admin['id'],'viewer')
        client,account_id=self.account('operator')
        self.assertEqual(client.get('/settings').status_code,200)
        self.store.change_role(account_id,'viewer')
        self.assertEqual(client.get('/api/routers').status_code,401)
        with self.assertRaises(ValueError): self.store.change_role(account_id,'superuser')


class RouterRestartTests(unittest.TestCase):
    def test_huawei_sends_reboot_control_one_once(self):
        from router_clients.huawei import HuaweiRouterClient
        router=HuaweiRouterClient('http://router.invalid','admin','test')
        router.login=Mock();router._get_page_tokens=Mock(return_value=['csrf'])
        router.session.post=Mock(return_value=Mock(text='<response>OK</response>'))
        router.restart()
        router.session.post.assert_called_once()
        args,kwargs=router.session.post.call_args
        self.assertTrue(args[0].endswith('/api/device/control'))
        self.assertIn('<Control>1</Control>',kwargs['data'])
        self.assertEqual(kwargs['headers']['__RequestVerificationToken'],'csrf')
        router.session.post.reset_mock(side_effect=True)
        router.session.post.side_effect=requests.Timeout()
        with self.assertRaises(requests.Timeout):router.restart()
        router.session.post.assert_called_once()

    def test_zte_sends_authenticated_reboot_once(self):
        from router_clients.zte import ZteRouterClient
        router=ZteRouterClient('http://router.invalid',session_cookie='zsidn=test-cookie')
        router._require_session_cookie=Mock();router._get_cmd=Mock(return_value={});router._ad_value=Mock(return_value='test-challenge')
        router.session.post=Mock(return_value=Mock(json=Mock(return_value={'result':'success'})))
        router.restart()
        router.session.post.assert_called_once()
        args,kwargs=router.session.post.call_args
        self.assertEqual(kwargs['data']['goformId'],'REBOOT_DEVICE')
        self.assertEqual(kwargs['data']['AD'],'test-challenge')
        self.assertIn('test-cookie',kwargs['headers']['Cookie'])
