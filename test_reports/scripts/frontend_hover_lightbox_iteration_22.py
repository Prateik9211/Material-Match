"""Playwright script body used by mcp_browser_automation for iteration 22.

This file is a test artifact recording the focused UI checks that were run on
the preview origin: hover highlight/dimming and shortlist swatch lightbox open
and close behavior.
"""

SCRIPT = r'''
try:
    await page.set_viewport_size({"width": 1920, "height": 1080})
    await page.wait_for_load_state("networkidle")
    await page.get_by_test_id("auth-email-input").fill("qa-lightbox-iter21-1784450933@materialmatch.ai")
    await page.get_by_test_id("auth-password-input").fill("Designer2026!")
    await page.get_by_test_id("auth-submit-btn").click()
    await page.wait_for_timeout(2500)
    await page.goto("https://design-match-ai.preview.emergentagent.com/projects/6a5c8f75f05770d6130c21aa/analysis")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_selector('[data-testid="materials-section"]', timeout=20000)

    first_card = page.get_by_test_id("material-card-0")
    second_card = page.get_by_test_id("material-card-1")
    await first_card.hover()
    await page.wait_for_timeout(500)
    first_class = await first_card.get_attribute("class")
    second_class = await second_card.get_attribute("class")
    if not first_class or "scale-[1.025]" not in first_class or "ring-4" not in first_class:
        raise Exception("Focused material card did not apply scale + ring hover highlight classes")
    if not second_class or "opacity-40" not in second_class or "scale-[0.99]" not in second_class:
        raise Exception("Non-focused material card did not dim while another card is hovered")

    await page.get_by_test_id("recommended-shortlist-btn-0-0").click()
    await page.wait_for_selector('[data-testid="shortlist-swatch-0"]', timeout=10000)
    await page.get_by_test_id("shortlist-swatch-0").click()
    await page.wait_for_selector('[data-testid="shortlist-swatch-lightbox"]', timeout=5000)
    if await page.get_by_test_id("shortlist-lightbox-image").count() < 1:
        raise Exception("Lightbox opened but did not show image preview")
    await page.get_by_test_id("shortlist-lightbox-close").click()
    await page.wait_for_selector('[data-testid="shortlist-swatch-lightbox"]', state="hidden", timeout=5000)

    await page.get_by_test_id("shortlist-swatch-0").click()
    await page.wait_for_selector('[data-testid="shortlist-swatch-lightbox"]', timeout=5000)
    await page.keyboard.press("Escape")
    await page.wait_for_selector('[data-testid="shortlist-swatch-lightbox"]', state="hidden", timeout=5000)

    await page.get_by_test_id("shortlist-swatch-0").click()
    await page.wait_for_selector('[data-testid="shortlist-swatch-lightbox"]', timeout=5000)
    await page.mouse.click(30, 30)
    await page.wait_for_selector('[data-testid="shortlist-swatch-lightbox"]', state="hidden", timeout=5000)
except Exception:
    raise
'''