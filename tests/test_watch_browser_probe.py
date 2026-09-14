import unittest

from test_watch import CHRONO, schema_html, product
import watch_browser_probe as probe


class BrowserProbeTests(unittest.TestCase):
    def test_http_200_challenge_is_not_success(self):
        result = probe.inspect_search(200, '<title>Verify you are human</title>')
        self.assertEqual(result['status'], 'unrecognized_or_challenge')
        self.assertEqual(result['links'], [])

    def test_403_cannot_be_success_even_with_links(self):
        result = probe.inspect_search(403, f'<a href="{CHRONO}">Hamilton</a>')
        self.assertEqual(result['status'], 'http_blocked')

    def test_real_search_links(self):
        result = probe.inspect_search(200, f'<a href="{CHRONO}">Hamilton</a>')
        self.assertEqual(result['status'], 'readable')
        self.assertEqual(result['links'], [CHRONO])

    def test_sweden_offer(self):
        result = probe.inspect_detail(200, schema_html(), CHRONO)
        self.assertEqual(result['status'], 'verified_sweden_offer')
        self.assertEqual(result['destination'], 'SE')
        self.assertEqual(result['total_sek'], 11100)

    def test_wrong_shipping_destination(self):
        p = product()
        p['offers']['shippingDetails']['shippingDestination']['addressCountry'] = 'US'
        self.assertEqual(probe.inspect_detail(200, schema_html(p), CHRONO)['status'], 'ineligible')

    def test_challenge_instead_of_product(self):
        self.assertEqual(probe.inspect_detail(200, '<h1>Just a moment</h1>', CHRONO)['status'],
                         'unrecognized_or_challenge')

    def test_blocked_product(self):
        self.assertEqual(probe.inspect_detail(429, schema_html(), CHRONO)['status'], 'http_blocked')

    def test_unknown_http_status(self):
        self.assertEqual(probe.inspect_search(None, '')['status'], 'http_error')


if __name__ == '__main__':
    unittest.main()
