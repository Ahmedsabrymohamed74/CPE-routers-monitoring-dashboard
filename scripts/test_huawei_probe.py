"""Offline safety checks: python -m unittest discover -s scripts -p 'test_huawei_probe.py'."""
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "resources"))
from router_clients.huawei import HuaweiRouterClient
from router_clients.huawei_probe import parse_neighbor_cells


SIGNAL = '<response><pci>17</pci><cell_id>123</cell_id><earfcn>1650</earfcn><band>B3</band></response>'


def response(body, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = body.encode()
    return result


class ProbeTests(unittest.TestCase):
    def client(self, replies):
        client = HuaweiRouterClient('http://router.invalid', 'test-user', 'test-password')
        client.session.get = Mock(side_effect=replies)
        client.session.post = Mock(side_effect=AssertionError('Unexpected POST'))
        return client

    def test_failures_continue_and_only_get(self):
        client = self.client([response(SIGNAL), requests.Timeout(),
                              response('<error><code>100002</code></error>'),
                              response('missing', 404), response('<response><mode>0</mode></response>'),
                              response(SIGNAL)])
        report = client.probe_cell_lock_capabilities()
        self.assertEqual([r['result'] for r in report['probes']],
                         ['unknown', 'unsupported', 'unsupported', 'supported'])
        self.assertTrue(report['serving_cell_unchanged'])
        self.assertTrue(report['connectivity_after'])
        client.session.post.assert_not_called()
        self.assertEqual(client.session.get.call_count, 6)
        for call in client.session.get.call_args_list:
            self.assertFalse(call.kwargs['allow_redirects'])

    def test_nested_fields_token_retry_and_redaction(self):
        client = self.client([response(SIGNAL), response('<error><code>125003</code></error>'),
                              response('<response><cells><cell><EARFCN>42</EARFCN><PCI>7</PCI>'
                                       '<SessionID>private</SessionID><password>test-password</password>'
                                       '</cell></cells></response>'),
                              response('<response><celllockStatus>0</celllockStatus></response>'),
                              response('<error><code>100003</code></error>'),
                              response('<error><code>100003</code></error>'),
                              response('<response><mode>0</mode></response>'), response(SIGNAL)])
        client._get_page_tokens = Mock(return_value=['private-token'])
        report = client.probe_cell_lock_capabilities()
        neighbor = report['probes'][0]
        self.assertTrue(neighbor['token_sent'])
        self.assertEqual(neighbor['result'], 'supported')
        self.assertIn('[REDACTED]', neighbor['body'])
        self.assertNotIn('test-password', str(report))
        self.assertNotIn('private-token', str(report))
        self.assertTrue(any('EARFCN' in field['path'] for field in neighbor['fields']))
        self.assertEqual(report['probes'][2]['result'], 'permission denied')
        client.session.post.assert_not_called()

    def test_lock_freq_retries_once_with_existing_token(self):
        client = self.client([response(SIGNAL), response('<response/>'), response('missing', 404),
                              response('missing', 404), response('<error><code>125003</code></error>'),
                              response('<response><LtePci>124</LtePci></response>'), response(SIGNAL)])
        client.headers['__RequestVerificationToken'] = 'existing-token'
        client._get_page_tokens = Mock(side_effect=AssertionError('Unexpected token fetch'))
        report = client.probe_cell_lock_capabilities()
        lock_freq = report['probes'][3]
        self.assertEqual(lock_freq['result'], 'supported')
        self.assertTrue(lock_freq['token_sent'])
        self.assertEqual(lock_freq['fields'][0]['path'], 'response/LtePci[0]')
        self.assertEqual(lock_freq['attempts'][0]['api_error_code'], '125003')
        calls = client.session.get.call_args_list
        self.assertNotIn('__RequestVerificationToken', calls[4].kwargs['headers'])
        self.assertEqual(calls[5].kwargs['headers']['__RequestVerificationToken'], 'existing-token')
        self.assertEqual(report['serving_before']['response/band[3]'], 'B3')
        client._get_page_tokens.assert_not_called()
        client.session.post.assert_not_called()

    def test_lock_freq_does_not_fetch_missing_token(self):
        client = self.client([response(SIGNAL), response('<response/>'), response('missing', 404),
                              response('missing', 404), response('<error><code>125002</code></error>'),
                              response(SIGNAL)])
        client._get_page_tokens = Mock(side_effect=AssertionError('Unexpected token fetch'))
        report = client.probe_cell_lock_capabilities()
        self.assertEqual(report['probes'][3]['token_retry'], 'existing token unavailable')
        self.assertTrue(report['connectivity_after'])
        client._get_page_tokens.assert_not_called()
        client.session.post.assert_not_called()

    def test_observed_lte_rows_and_unknown_width(self):
        parsed = parse_neighbor_cells([{'path': 'response/nbrcell_ltelist[0]',
                                        'value': '3725,B8,124,-83dBm,-12dB,-54dBm,--;unexpected,row;'}])
        self.assertEqual(parsed['lte'][0]['pci'], '124')
        self.assertEqual(parsed['lte'][0]['earfcn'], '3725')
        self.assertIsNone(parsed['lte'][0]['sinr'])
        self.assertEqual(parsed['unparsed_rows'], ['unexpected,row'])

    def test_signal_failure_skips_probes(self):
        client = self.client([response('<html>Login</html>')])
        report = client.probe_cell_lock_capabilities()
        self.assertIn('aborted', report)
        self.assertEqual(report['probes'], [])
        self.assertEqual(client.session.get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
