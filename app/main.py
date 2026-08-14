from __future__ import annotations
import argparse
import json
from .config import load_config
from .google_images import BrowserLaunchError, GoogleImagesBrowser
from .runner import run


def main():
    ap = argparse.ArgumentParser(description="Local Google Images rank tracker (no CAPTCHA bypass).")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--diagnose", action="store_true", help="capture a read-only browser environment report")
    ap.add_argument("--diagnose-keyword", default="Albert Einstein")
    args = ap.parse_args()
    cfg = load_config(args.config)
    if args.validate:
        print("Configuration OK")
        print(f"daily_limit={cfg.daily_limit}")
        print(f"run_hours={cfg.run_hours}")
        print(f"interval_seconds={cfg.interval_seconds:.2f}")
        print(f"input_csv={cfg.input_csv}")
        print(f"profile_dir={cfg.profile_dir}")
        return
    if args.diagnose:
        browser = GoogleImagesBrowser(cfg)
        try:
            try:
                browser.start()
            except BrowserLaunchError as exc:
                path = browser.save_launch_failure_diagnostic(cfg.log_dir / "diagnostics", exc)
                print("Browser launch failed; a diagnostic report was still created.")
                print(f"Diagnostic report saved to: {path}")
                raise SystemExit(3) from None
            report, path = browser.diagnose(cfg.log_dir / "diagnostics", args.diagnose_keyword)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"Diagnostic report saved to: {path}")
            if report["process_exit_code"]:
                raise SystemExit(report["process_exit_code"])
        finally:
            browser.close()
        return
    run(cfg, args.limit)

if __name__ == "__main__":
    main()
