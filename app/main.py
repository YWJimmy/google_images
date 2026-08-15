from __future__ import annotations
import argparse
import json
from .config import load_config
from .google_images import BrowserLaunchError, GoogleImagesBrowser
from .runner import run
from .state_capture import StateCaptureError, capture_google_state
from .storage import load_tasks


def main():
    ap = argparse.ArgumentParser(description="Local Google Images rank tracker (no CAPTCHA bypass).")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--diagnose", action="store_true", help="capture a read-only browser environment report")
    ap.add_argument("--diagnose-keyword", default="Albert Einstein")
    ap.add_argument("--probe-first-image", action="store_true", help="click one image and report its structure")
    ap.add_argument("--probe-keyword", default="Albert Einstein")
    ap.add_argument("--source-domain-test", action="store_true", help="run a bounded top-N source-domain test")
    ap.add_argument("--test-max-results", type=int, default=100)
    ap.add_argument("--test-time-budget-seconds", type=float, default=300)
    ap.add_argument("--capture-state", action="store_true", help="capture state from a manually opened Chrome")
    ap.add_argument("--cdp-endpoint", default="http://127.0.0.1:9222")
    args = ap.parse_args()
    if args.limit is not None and args.limit <= 0:
        ap.error("--limit must be > 0")
    cfg = load_config(args.config)
    if args.capture_state:
        try:
            summary = capture_google_state(args.cdp_endpoint, cfg.storage_state_path)
        except StateCaptureError as exc:
            print(f"State capture failed: {exc}")
            raise SystemExit(3) from None
        print("Google state captured without printing cookie values.")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return
    if args.validate:
        print("Configuration OK")
        print(f"daily_limit={cfg.daily_limit}")
        print(f"run_hours={cfg.run_hours}")
        print(f"interval_seconds={cfg.interval_seconds:.2f}")
        print(f"input_csv={cfg.input_csv}")
        print(f"profile_dir={cfg.profile_dir}")
        print(f"session_mode={cfg.session_mode}")
        print(f"storage_state_path={cfg.storage_state_path}")
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
    if args.probe_first_image:
        browser = GoogleImagesBrowser(cfg)
        try:
            try:
                browser.start()
            except BrowserLaunchError as exc:
                path = browser.save_launch_failure_diagnostic(cfg.log_dir / "diagnostics", exc)
                print("Browser launch failed; a diagnostic report was still created.")
                print(f"Diagnostic report saved to: {path}")
                raise SystemExit(3) from None
            report, path = browser.probe_first_image(
                cfg.log_dir / "diagnostics", args.probe_keyword
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"Probe report saved to: {path}")
            if report["process_exit_code"]:
                raise SystemExit(report["process_exit_code"])
        finally:
            browser.close()
        return
    if args.source_domain_test:
        sample_limit = args.limit or 10
        if not (1 <= args.test_max_results <= 100):
            raise SystemExit("--test-max-results must be between 1 and 100")
        if args.test_time_budget_seconds <= 0:
            raise SystemExit("--test-time-budget-seconds must be > 0")
        tasks = load_tasks(cfg.input_csv, sample_limit)
        browser = GoogleImagesBrowser(cfg)
        try:
            try:
                browser.start()
            except BrowserLaunchError as exc:
                path = browser.save_launch_failure_diagnostic(cfg.log_dir / "diagnostics", exc)
                print("Browser launch failed; a diagnostic report was still created.")
                print(f"Diagnostic report saved to: {path}")
                raise SystemExit(3) from None
            report, path = browser.test_top_image_sources(
                cfg.log_dir / "diagnostics",
                tasks,
                args.test_max_results,
                args.test_time_budget_seconds,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"Source-domain test report saved to: {path}")
            if report["process_exit_code"]:
                raise SystemExit(report["process_exit_code"])
        finally:
            browser.close()
        return
    process_exit_code = run(cfg, args.limit)
    if process_exit_code:
        raise SystemExit(process_exit_code)

if __name__ == "__main__":
    main()
