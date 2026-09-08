"""Optional local browser smoke check; needs Playwright only for development."""
from pathlib import Path
import sys
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser = p.chromium.launch(channel='msedge', headless=True)
    page = browser.new_page(viewport={'width': 1280, 'height': 950})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.route('https://**/*', lambda route: route.abort())
    page.goto((root / 'output' / 'angebote.html').as_uri())
    page.wait_for_selector('#count')
    count = page.locator('.card').count()
    assert count > 0, 'Expected live result cards'
    page.locator('#max').fill('0')
    assert page.locator('.card').count() == 0
    page.locator('#max').fill('4000')
    assert page.locator('.card').count() == count
    page.locator('#query').fill('nonexistent-title-zzzz')
    assert page.locator('.card').count() == 0
    page.locator('#query').fill('')
    page.locator('#delivery').select_option('shipping')
    page.screenshot(path=str(root / 'output' / 'dashboard-desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile overflow'
    page.screenshot(path=str(root / 'output' / 'dashboard-mobile.png'), full_page=True)
    assert not errors, errors
    print(f'Dashboard OK: {count} cards, filters work, mobile fits, no JS errors.')
    browser.close()
