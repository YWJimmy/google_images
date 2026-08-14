from __future__ import annotations
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
from .config import load_config


def main():
    ap = argparse.ArgumentParser(description="Open the dedicated Chrome profile for manual setup/inspection.")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()
    cfg = load_config(Path(args.config))
    cfg.profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(cfg.profile_dir),
            channel=cfg.browser_channel,
            headless=False,
            viewport={"width": cfg.viewport_width, "height": cfg.viewport_height},
            args=["--disable-notifications"],
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.google.com/?hl=" + cfg.hl)
        print("Dedicated profile opened.")
        print("You may manually review/accept normal Google consent in this window.")
        print("Do not use this helper to automate or bypass CAPTCHA/challenges.")
        input("Press ENTER here when finished; Chrome will close... ")
        context.close()

if __name__ == "__main__":
    main()
