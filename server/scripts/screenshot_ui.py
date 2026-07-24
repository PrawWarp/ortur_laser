"""Capture UI screenshots for visual QA."""
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent.parent / ".qa_screenshots"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "ui-idle.png"), full_page=False)

        # Switch to canvas + type text so preview has content
        page.click('button.tab[data-tab="canvas"]')
        page.fill("#canvasText", "HENRY")
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "ui-canvas.png"), full_page=False)

        # Create job — card + run bar should update
        page.click("#btnCreate")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "ui-job-ready.png"), full_page=False)

        # Open arm modal (visual QA of custom checklist dialog)
        page.evaluate(
            """() => {
              openModal({
                title: 'Arm laser',
                bodyHtml: `<p>This unlocks live laser output. Check each item before continuing:</p>
                  <div class="modal-check">
                    <label><input type="checkbox" /> Workspace is clear of flammables and hands</label>
                    <label><input type="checkbox" /> Eye protection is on</label>
                    <label><input type="checkbox" /> Exhaust / ventilation is ready</label>
                  </div>`,
                okText: 'ARM',
                cancelText: 'Cancel',
                danger: true,
                requireChecks: true,
              });
            }"""
        )
        page.wait_for_selector("#appModal:not(.hidden)")
        page.wait_for_timeout(300)
        page.screenshot(path=str(OUT / "ui-arm-modal.png"), full_page=False)

        browser.close()
    print("screenshots ->", OUT)


if __name__ == "__main__":
    main()
