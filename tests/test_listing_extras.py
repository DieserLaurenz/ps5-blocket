import json
import unittest

import scraper as s


def hydration(identifier='123', images=None, profile=None):
    state = {'loaderData': {'item-recommerce': {
        'itemData': {'meta': {'adId': identifier}, 'images': images or []},
        'profileData': profile}}}
    return '<script>window.__staticRouterHydrationData = JSON.parse(' + json.dumps(json.dumps(state)) + ');</script>'


class ListingExtrasTests(unittest.TestCase):
    def test_gallery_is_scoped_deduplicated_and_resized(self):
        urls = ['https://images.blocketcdn.se/dynamic/default/item/123/a',
                'https://images.blocketcdn.se/dynamic/1280w/item/123/a',
                'https://images.blocketcdn.se/dynamic/default/item/123/b',
                'https://images.blocketcdn.se/dynamic/default/item/999/recommendation',
                'https://images.blocketcdn.se/dynamic/default/profile/avatar',
                'https://evil.invalid/dynamic/default/item/123/a',
                'https://images.blocketcdn.se@evil.invalid/dynamic/default/item/123/a',
                'http://images.blocketcdn.se/dynamic/default/item/123/a']
        extras = s.parse_listing_extras(hydration(images=[{'uri': u} for u in urls]), '123')
        self.assertEqual(extras['photos'], [f'https://images.blocketcdn.se/dynamic/960w/item/123/{i}' for i in 'ab'])

    def test_product_and_marketing_ratings_are_not_seller_ratings(self):
        product = {'@type': 'Product', 'sku': '123', 'aggregateRating': {'ratingValue': 5, 'ratingCount': 999},
                   'image': 'https://images.blocketcdn.se/dynamic/1280w/item/123/a'}
        source = '<script type="application/ld+json">' + json.dumps(product) + '</script>'
        source += '<div>Example profile: 16 reviews, rating 8.7</div>' + hydration()
        extras = s.parse_listing_extras(source, '123')
        self.assertIsNone(extras['seller_rating'])
        self.assertEqual(len(extras['photos']), 1)

    def test_explicit_seller_aggregate_and_review_count(self):
        rating = {'ratingValue': '4,8', 'reviewCount': '12', 'bestRating': '5'}
        source = hydration(profile={'aggregateRating': rating})
        expected = {'score': 4.8, 'count': 12, 'best': 5}
        self.assertEqual(s.parse_listing_extras(source, '123')['seller_rating'], expected)
        product = {'@type': 'Product', 'sku': '123', 'offers': {'seller': {'aggregateRating': rating}}}
        source = '<script type="application/ld+json">' + json.dumps(product) + '</script>'
        self.assertEqual(s.parse_listing_extras(source, '123')['seller_rating'], expected)
        self.assertIsNone(s.parse_listing_extras(source, '999')['seller_rating'])

    def test_missing_scale_is_not_invented_and_bad_ratings_are_rejected(self):
        self.assertIsNone(s.seller_rating({'ratingValue': 0, 'ratingCount': 0}))
        self.assertIsNone(s.seller_rating({'ratingValue': 'nan', 'ratingCount': 10}))
        self.assertIsNone(s.seller_rating({'ratingValue': 6, 'ratingCount': 10, 'bestRating': 5}))
        self.assertIsNone(s.seller_rating({'ratingValue': True, 'ratingCount': 10}))
        self.assertIsNone(s.seller_rating({'ratingValue': 4, 'ratingCount': 1.5}))
        self.assertIsNone(s.seller_rating({'ratingValue': 4}))
        self.assertIsNone(s.seller_rating({'ratingValue': 4, 'ratingCount': 12})['best'])

    def test_bad_or_other_listing_metadata_is_ignored(self):
        self.assertEqual(s.parse_listing_extras('<script>window.__staticRouterHydrationData = JSON.parse(bad);</script>', '123')['photos'], [])
        source = hydration('999', [{'uri': 'https://images.blocketcdn.se/dynamic/default/item/123/a'}])
        self.assertEqual(s.parse_listing_extras(source, '123')['photos'], [])


if __name__ == '__main__':
    unittest.main()
