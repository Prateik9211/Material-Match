"""Playwright script body used by mcp_browser_automation for iteration 24.

Focused regression check for catalogue Preview lightbox portal/fullscreen behavior.
Seed data: project 6a5c9aafa5fed9d31d8790ea, user qa-catalogue-preview-iter23-1784453806@materialmatch.ai.
"""

SCRIPT = r'''
try:
    await page.set_viewport_size({"width": 1920, "height": 1080})
    print("Viewport set to 1920x1080")

    requests_after_open = []
    track_requests = False
    def on_request(req):
        if track_requests:
            requests_after_open.append(req.url)
    page.on("request", on_request)

    await page.goto("https://design-match-ai.preview.emergentagent.com/auth")
    await page.wait_for_load_state("networkidle")
    print("Auth page loaded")

    await page.get_by_test_id("auth-email-input").fill("qa-catalogue-preview-iter23-1784453806@materialmatch.ai")
    await page.get_by_test_id("auth-password-input").fill("Designer2026!")
    await page.get_by_test_id("auth-submit-btn").click()
    await page.wait_for_timeout(2500)
    print("Logged in as seeded regular user")

    await page.goto("https://design-match-ai.preview.emergentagent.com/projects/6a5c9aafa5fed9d31d8790ea/analysis")
    await page.wait_for_load_state("networkidle")
    await page.wait_for_selector('[data-testid="materials-section"]', timeout=20000)
    print("Seeded analysis page loaded")

    card = page.get_by_test_id("material-card-0")
    await card.hover()
    await page.wait_for_timeout(500)
    card_class = await card.get_attribute("class")
    if not card_class or "scale-[1.025]" not in card_class or "-translate-y-1" not in card_class:
        raise Exception(f"Hovered/focused material card did not have expected transform classes: {card_class}")
    print("Hovered/focused card has scale/translate transform classes")

    # Image branch + portal/fullscreen + backdrop close. Track only requests made after opening preview.
    requests_after_open.clear()
    track_requests = True
    await page.get_by_test_id("recommended-preview-btn-0-0").click()
    await page.wait_for_selector('[data-testid="catalogue-preview-lightbox"]', timeout=5000)
    await page.wait_for_timeout(900)
    track_requests = False
    print("Recommended image preview opened")

    overlay = page.get_by_test_id("catalogue-preview-lightbox")
    box = await overlay.bounding_box()
    if not box:
        raise Exception("Could not measure lightbox bounding box")
    print(f"Overlay bounding box: {box}")
    if abs(box["x"]) > 6 or abs(box["y"]) > 6 or abs(box["width"] - 1920) > 6 or abs(box["height"] - 1080) > 6:
        raise Exception(f"Lightbox is not fullscreen at 1920x1080: {box}")

    parent_tag = await overlay.evaluate("el => el.parentElement && el.parentElement.tagName")
    if parent_tag != "BODY":
        raise Exception(f"Lightbox is not portaled to document.body; parent tag was {parent_tag}")
    print("Lightbox is portaled under BODY")

    if await page.get_by_test_id("catalogue-preview-image").count() != 1:
        raise Exception("Image branch did not render catalogue-preview-image for swatch_crop_b64 match")
    image_src = await page.get_by_test_id("catalogue-preview-image").get_attribute("src")
    if not image_src or not image_src.startswith("data:image/jpeg;base64,"):
        raise Exception("Image branch src is not a data:image/jpeg;base64 URL")
    print("Image branch rendered correctly")

    backend_requests = [u for u in requests_after_open if "/api/" in u]
    if backend_requests:
        raise Exception(f"Opening image preview fired backend API requests: {backend_requests}")
    print("No backend API requests fired while opening image preview")

    await page.mouse.click(20, 20)
    await page.wait_for_selector('[data-testid="catalogue-preview-lightbox"]', state="hidden", timeout=5000)
    print("Backdrop click at (20,20) closed image preview")

    # ESC closes.
    await card.hover()
    await page.wait_for_timeout(200)
    await page.get_by_test_id("recommended-preview-btn-0-0").click()
    await page.wait_for_selector('[data-testid="catalogue-preview-lightbox"]', timeout=5000)
    await page.keyboard.press("Escape")
    await page.wait_for_selector('[data-testid="catalogue-preview-lightbox"]', state="hidden", timeout=5000)
    print("ESC closed preview")

    # Close button closes.
    await card.hover()
    await page.wait_for_timeout(200)
    await page.get_by_test_id("recommended-preview-btn-0-0").click()
    await page.wait_for_selector('[data-testid="catalogue-preview-lightbox"]', timeout=5000)
    await page.get_by_test_id("catalogue-preview-close").click()
    await page.wait_for_selector('[data-testid="catalogue-preview-lightbox"]', state="hidden", timeout=5000)
    print("Close button closed preview")

    # Hex branch should render for the catalogue alternative with color_hex only; also no backend preview/admin request.
    await card.hover()
    await page.wait_for_timeout(200)
    requests_after_open.clear()
    track_requests = True
    await page.get_by_test_id("catalogue-preview-btn-0-1").click()
    await page.wait_for_selector('[data-testid="catalogue-preview-lightbox"]', timeout=5000)
    await page.wait_for_timeout(900)
    track_requests = False
    print("Catalogue hex preview opened")

    if await page.get_by_test_id("catalogue-preview-hex").count() != 1:
        raise Exception("Hex branch did not render catalogue-preview-hex for color_hex-only match")
    hex_bg = await page.get_by_test_id("catalogue-preview-hex").evaluate("el => getComputedStyle(el).backgroundColor")
    if "241" not in hex_bg or "235" not in hex_bg or "224" not in hex_bg:
        raise Exception(f"Hex branch background color did not match #F1EBE0; got {hex_bg}")
    print(f"Hex branch rendered correctly with background {hex_bg}")

    overlay2 = page.get_by_test_id("catalogue-preview-lightbox")
    box2 = await overlay2.bounding_box()
    print(f"Hex overlay bounding box: {box2}")
    if not box2 or abs(box2["x"]) > 6 or abs(box2["y"]) > 6 or abs(box2["width"] - 1920) > 6 or abs(box2["height"] - 1080) > 6:
        raise Exception(f"Hex lightbox is not fullscreen at 1920x1080: {box2}")

    backend_requests_hex = [u for u in requests_after_open if "/api/" in u]
    if backend_requests_hex:
        raise Exception(f"Opening hex preview fired backend API requests: {backend_requests_hex}")
    print("No backend API requests fired while opening hex preview")

    await page.mouse.click(20, 20)
    await page.wait_for_selector('[data-testid="catalogue-preview-lightbox"]', state="hidden", timeout=5000)
    print("Backdrop click at (20,20) closed hex preview")

    # Get error messages using specific selectors
    error_text = await page.evaluate("""() => {
    const errorElements = Array.from(document.querySelectorAll('.error, [class*="error"], [id*="error"]'));
    return errorElements.map(el => el.textContent).join(", ");
    }""")
    if error_text:
        print(f"Found error message: {error_text}")
    else:
        print("No error messages found on the page")

    print("CATALOGUE_PREVIEW_PORTAL_TEST_PASS")
except Exception as e:
    print(f"CATALOGUE_PREVIEW_PORTAL_TEST_FAIL: {e}")
    raise
'''
