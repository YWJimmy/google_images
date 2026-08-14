from __future__ import annotations
import argparse
from .config import load_config
from .runner import run


def main():
    ap = argparse.ArgumentParser(description="Local Google Images rank tracker (no CAPTCHA bypass).")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--validate", action="store_true")
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
    run(cfg, args.limit)

if __name__ == "__main__":
    main()
