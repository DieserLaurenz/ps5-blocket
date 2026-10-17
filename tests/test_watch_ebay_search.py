import unittest
from watch_sources import listing_links


class EbaySearchTests(unittest.TestCase):
    def test_placeholder_and_other_models_do_not_break_exact_results(self):
        html = '''<a href="https://ebay.com/itm/123456">Shop on eBay</a>
        <a href="https://other.test/itm/999999">H36215140</a>
        <a href="https://www.ebay.com/itm/111111">Hamilton H52414130</a>
        <a href="https://www.ebay.com/itm/800778739305?tracking=1">Hamilton H36215140</a>
        <a href="https://www.ebay.com/itm/800778739305">Hamilton H36215140</a>'''
        links, _ = listing_links('ebay', html)
        self.assertEqual(links, ['https://www.ebay.com/itm/800778739305'])

    def test_unrecognized_page_not_reported_as_empty_market(self):
        with self.assertRaises(ValueError):
            listing_links('ebay', '<a href="https://ebay.com/itm/123456">Shop on eBay</a>')
