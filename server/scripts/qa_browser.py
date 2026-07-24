"""Browser QA: version chip, Settings/Updates, layout (no laser required)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).resolve().parent.parent / ".qa_screenshots"
OUT.mkdir(parents=True, exist_ok=True)

fails: list[str] = []
notes: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print("  OK ", msg)
    else:
        print(" FAIL", msg)
        fails.append(msg)


def note(msg: str) -> None:
    print(" NOTE", msg)
    notes.append(msg)


def box_info(page, selector: str) -> dict:
    return page.locator(selector).evaluate(
        """(el) => {
          const r = el.getBoundingClientRect();
          const cs = getComputedStyle(el);
          return {
            text: (el.textContent || '').trim(),
            x: r.x, y: r.y, w: r.width, h: r.height,
            right: r.right, bottom: r.bottom,
            overflow: cs.overflow, visibility: cs.visibility,
            display: cs.display, opacity: cs.opacity,
          };
        }"""
    )


def fully_visible(page, selector: str, viewport: dict) -> tuple[bool, dict]:
    info = box_info(page, selector)
    ok = (
        info["w"] > 2
        and info["h"] > 2
        and info["x"] >= -1
        and info["y"] >= -1
        and info["right"] <= viewport["width"] + 1
        and info["bottom"] <= viewport["height"] + 1
        and info["visibility"] != "hidden"
        and info["display"] != "none"
        and float(info["opacity"] or 1) > 0.1
    )
    return ok, info


def main() -> int:
    viewport = {"width": 1440, "height": 900}
    report: dict = {"checks": [], "screenshots": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport)
        page.goto(BASE, wait_until="networkidle")
        page.wait_for_timeout(900)

        print("=== HEADER / VERSION ===")
        ver = page.locator(".ver")
        check(ver.count() == 1, "version chip (.ver) present")
        vtext = ver.inner_text().strip()
        check(vtext.startswith("v"), f"version text starts with v ({vtext!r})")
        check("0.2" in vtext, f"version looks like 0.2.x ({vtext!r})")
        ok, info = fully_visible(page, ".ver", viewport)
        check(ok, f"version chip fully in viewport ({info})")
        # Ensure version isn't clipped by overflow on brand
        brand_clip = page.locator(".brand").evaluate(
            """(el) => {
              const ver = el.querySelector('.ver');
              if (!ver) return {ok:false, reason:'no ver'};
              const br = el.getBoundingClientRect();
              const vr = ver.getBoundingClientRect();
              return {
                ok: vr.right <= br.right + 1 && vr.left >= br.left - 1,
                brandRight: br.right, verRight: vr.right, verText: ver.textContent.trim()
              };
            }"""
        )
        check(brand_clip.get("ok"), f"version not clipped by .brand ({brand_clip})")

        title = page.locator("h1").inner_text().strip()
        check(title == "Ortur Engraver", f"title is Ortur Engraver ({title!r})")

        page.screenshot(path=str(OUT / "qa-header.png"), full_page=False)
        report["screenshots"].append("qa-header.png")

        print("=== STUDIO NAV ===")
        for name in ("source", "size", "material", "preview", "run", "settings", "misc"):
            btn = page.locator(f'button.studio-nav-btn[data-studio="{name}"]')
            check(btn.count() == 1, f"nav has {name}")

        print("=== SETTINGS PANE ===")
        page.click('button.studio-nav-btn[data-studio="settings"]')
        page.wait_for_timeout(500)
        pane = page.locator("#studio-settings")
        check(pane.evaluate("el => el.classList.contains('active')"), "settings pane active")
        for sel in (
            "#cfgSerialPort",
            "#cfgSerialBaud",
            "#cfgBedW",
            "#cfgBedH",
            "#cfgHttpPort",
            "#cfgLanAccess",
            "#btnSaveSettings",
            "#btnResetSettings",
            "#btnCheckUpdate",
            "#btnApplyUpdate",
            "#updateCurrent",
            "#updateLatest",
            "#updateVersion",
        ):
            check(page.locator(sel).count() == 1, f"settings has {sel}")

        # Wait for settings + update fetch
        page.wait_for_timeout(2500)
        port_val = page.input_value("#cfgSerialPort")
        baud_val = page.input_value("#cfgSerialBaud")
        check(bool(port_val), f"serial port loaded ({port_val!r})")
        check(baud_val.isdigit(), f"baud loaded ({baud_val!r})")

        uv = page.locator("#updateVersion").inner_text().strip()
        check(uv.startswith("v"), f"Updates pane version ({uv!r})")

        cur = page.locator("#updateCurrent").inner_text().strip()
        lat = page.locator("#updateLatest").inner_text().strip()
        check(cur not in ("", "Checking…", "—"), f"update current populated ({cur!r})")
        check(lat not in ("", "—"), f"update latest populated ({lat!r})")
        note(f"update current={cur!r}")
        note(f"update latest={lat!r}")
        note(f"update status={page.locator('#updateStatus').inner_text().strip()!r}")

        page.screenshot(path=str(OUT / "qa-settings.png"), full_page=False)
        report["screenshots"].append("qa-settings.png")

        # Narrow viewport — version must still show
        print("=== NARROW VIEWPORT ===")
        page.set_viewport_size({"width": 1100, "height": 800})
        page.wait_for_timeout(400)
        ok_n, info_n = fully_visible(page, ".ver", {"width": 1100, "height": 800})
        check(ok_n, f"version visible at 1100px ({info_n})")
        page.screenshot(path=str(OUT / "qa-header-narrow.png"), full_page=False)
        report["screenshots"].append("qa-header-narrow.png")

        page.set_viewport_size(viewport)
        page.wait_for_timeout(200)

        print("=== MISC + ALARM BANNER ===")
        page.click('button.studio-nav-btn[data-studio="misc"]')
        page.wait_for_timeout(300)
        check(page.locator("#lanAccess").count() == 1, "misc LAN toggle present")
        # Banner should be hidden when disconnected / not Alarm
        hidden = page.locator("#alarmBanner").evaluate("el => el.classList.contains('hidden')")
        check(hidden, "alarm banner hidden when not in Alarm")

        print("=== RUN / CREATE (smoke) ===")
        page.click('button.studio-nav-btn[data-studio="source"]')
        page.click('button.src-tab[data-tab="canvas"]')
        page.fill("#canvasText", "QA")
        page.click('button.studio-nav-btn[data-studio="run"]')
        page.wait_for_timeout(200)
        page.click("#btnCreate")
        page.wait_for_timeout(1500)
        job_name = page.locator("#jobName").inner_text().strip()
        check(job_name != "No job yet", f"job created ({job_name!r})")
        page.screenshot(path=str(OUT / "qa-run.png"), full_page=False)
        report["screenshots"].append("qa-run.png")

        # Soft error:9 path shouldn't open modal — inject fake failure handler smoke via evaluate
        print("=== ERROR:9 SOFT PATH ===")
        has_soft = page.evaluate("() => /error:\\s*9/.test(String(postAct)) || /Busy — wait/.test(document.documentElement.innerHTML) || /Busy — wait for motion/.test(String(postAct))")
        # postAct is a function — check source in page scripts is hard; verify string in loaded JS via fetch
        js_src = page.evaluate("async () => (await fetch('/static/app.js')).text()")
        check("Busy — wait for motion to finish" in js_src, "app.js soft-handles error:9")
        check("/error:\\s*9\\b/i.test" in js_src or r"/error:\s*9\b/i.test" in js_src or "error:\\s*9" in js_src, "app.js detects error:9")

        browser.close()

    print()
    if fails:
        print(f"FAILED {len(fails)} checks")
        for f in fails:
            print(" -", f)
        return 1
    print("ALL BROWSER CHECKS PASSED")
    (OUT / "qa-report.json").write_text(
        json.dumps({"ok": True, "notes": notes, "screenshots": report["screenshots"]}, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
