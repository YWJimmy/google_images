from __future__ import annotations
from datetime import date
import logging
import time

from .config import Config
from .error_codes import describe
from .google_images import GoogleImagesBrowser, ChallengeDetected, ConsentRequired, BrowserLaunchError, NavigationError
from .models import SearchResult
from .ranking import hostname, domain_matches
from .storage import already_done, export_csv, load_tasks, open_db, save_result


def setup_logger(cfg: Config) -> logging.Logger:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("local_google_images")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh = logging.FileHandler(cfg.log_dir / f"run_{date.today().isoformat()}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(); sh.setFormatter(fmt)
    logger.addHandler(fh); logger.addHandler(sh)
    return logger


def run(cfg: Config, limit_override: int | None = None):
    logger = setup_logger(cfg)
    run_date = date.today().isoformat()
    limit = min(limit_override or cfg.daily_limit, cfg.daily_limit)
    tasks = load_tasks(cfg.input_csv, limit)
    if not tasks:
        raise ValueError("No valid tasks found")

    conn = open_db(cfg.database_path)
    export_path = cfg.output_dir / f"results_{run_date}.csv"
    pending = [task for task in tasks if not already_done(conn, run_date, task)]
    if not pending:
        export_csv(conn, run_date, export_path)
        conn.close()
        logger.info("Nothing pending for today. Output=%s", export_path)
        return

    interval = cfg.run_hours * 3600 / len(pending)
    browser = GoogleImagesBrowser(cfg)

    try:
        browser.start()
    except BrowserLaunchError as exc:
        logger.error("Browser launch failed: %s", exc)
        conn.close()
        raise

    logger.info(
        "Start: selected=%d pending=%d run_hours=%.2f interval=%.2fs",
        len(tasks), len(pending), cfg.run_hours, interval
    )
    t0 = time.monotonic()
    stop_run = False

    try:
        for idx, task in enumerate(pending):
            scheduled = t0 + idx * interval
            delay = scheduled - time.monotonic()
            if delay > 0:
                time.sleep(delay)

            logger.info("[%d/%d] SEARCH %s | target=%s", idx+1, len(pending), task.keyword, task.target_domain)
            items = []
            try:
                google_url, items, elapsed = browser.search(task.keyword, cfg.max_results)
                match = next((x for x in items if domain_matches(hostname(x.page_url), task.target_domain, cfg.include_subdomains)), None)

                if cfg.require_full_depth and len(items) < cfg.max_results:
                    code = -5
                    msg = f"collected {len(items)} of required {cfg.max_results} image-source URLs"
                    result = SearchResult(task.keyword, task.target_domain, code, describe(code),
                                          collected_count=len(items), google_url=google_url,
                                          elapsed_ms=elapsed, message=msg)
                elif match:
                    result = SearchResult(task.keyword, task.target_domain, match.rank, describe(match.rank),
                                          matched_rank=match.rank, matched_url=match.page_url,
                                          collected_count=len(items), google_url=google_url,
                                          elapsed_ms=elapsed)
                else:
                    result = SearchResult(task.keyword, task.target_domain, -1, describe(-1),
                                          collected_count=len(items), google_url=google_url,
                                          elapsed_ms=elapsed)

            except ChallengeDetected as exc:
                diag = browser.save_diagnostics(cfg.log_dir / "diagnostics", f"challenge_{run_date}_{idx+1:04d}")
                msg = str(exc) + (f" | diagnostics={'; '.join(diag)}" if diag else "")
                result = SearchResult(task.keyword, task.target_domain, -4, describe(-4), message=msg)
                stop_run = cfg.stop_on_challenge
            except ConsentRequired as exc:
                diag = browser.save_diagnostics(cfg.log_dir / "diagnostics", f"consent_{run_date}_{idx+1:04d}")
                msg = str(exc) + (f" | diagnostics={'; '.join(diag)}" if diag else "")
                result = SearchResult(task.keyword, task.target_domain, -9, describe(-9), message=msg)
                stop_run = True
            except NavigationError as exc:
                result = SearchResult(task.keyword, task.target_domain, -2, describe(-2), message=str(exc))
            except Exception as exc:
                result = SearchResult(task.keyword, task.target_domain, -8, describe(-8), message=repr(exc))

            save_result(conn, run_date, result, items)
            export_csv(conn, run_date, export_path)
            logger.info("[%d/%d] code=%d type=%s count=%d", idx+1, len(pending), result.result_code, result.result_type, result.collected_count)

            if result.message:
                logger.info("[%d/%d] detail=%s", idx+1, len(pending), result.message)

            if stop_run:
                logger.error("Google challenge/consent state detected. Run stopped; no bypass attempt will be made.")
                break
    finally:
        export_csv(conn, run_date, export_path)
        browser.close()
        conn.close()
        logger.info("Finished. Output=%s", export_path)
