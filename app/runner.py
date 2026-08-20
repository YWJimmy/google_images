from __future__ import annotations
# 本模块为 Google 图片自动搜索系统的一部分。
# 整体流程：任务调度 -> 浏览器自动化 -> Google页面解析 -> 结果返回 -> 状态记录。
# 以下注释仅用于解释工程设计，不改变任何执行逻辑。

from datetime import date
import logging
import time

from .config import Config
from .collection_telemetry import (
    CollectionTelemetry,
    profile_network_snapshot,
    telemetry_path,
)
from .error_codes import describe
from .google_images import (
    GoogleImagesBrowser,
    ChallengeDetected,
    ConsentRequired,
    BrowserLaunchError,
    NavigationError,
    SearchParseTimeout,
    format_search_metrics,
)
from .models import SearchResult
from .ranking_decision import decide_source_rank
from .storage import already_done, export_csv, load_tasks, open_db, save_result


# 功能：setup_logger 函数，负责当前模块中的一项具体处理逻辑。
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


# 功能：任务调度入口。
# 流程：读取配置 -> 调用搜索模块 -> 记录结果 -> 输出状态。

def run(cfg: Config, limit_override: int | None = None) -> int:
    logger = setup_logger(cfg)
    run_date = date.today().isoformat()
    if limit_override is not None and limit_override <= 0:
        raise ValueError("limit must be > 0")
    limit = min(limit_override if limit_override is not None else cfg.daily_limit, cfg.daily_limit)
    tasks = load_tasks(cfg.input_csv, limit)
    if not tasks:
        raise ValueError("No valid tasks found")

    conn = open_db(cfg.database_path)
# port表示网络端口，用于区分同一主机上的不同服务入口。
    export_path = cfg.output_dir / f"results_{run_date}.csv"
    pending = [task for task in tasks if not already_done(conn, run_date, task)]
    if not pending:
        export_csv(conn, run_date, export_path)
        conn.close()
# port表示网络端口，用于区分同一主机上的不同服务入口。
        logger.info("Nothing pending for today. Output=%s", export_path)
        return 0

    interval = cfg.run_hours * 3600 / len(pending)
    browser = GoogleImagesBrowser(cfg)
    telemetry = CollectionTelemetry(telemetry_path(cfg.log_dir))
    telemetry_run_id = None
    try:
        telemetry_run_id = telemetry.start_run(
            mode="daily_run",
            requested_count=len(pending),
            configured_delay_seconds=interval,
            session_mode=cfg.session_mode,
        )
    except Exception as exc:
        logger.warning("Telemetry start failed: %s", type(exc).__name__)

    try:
        browser.start()
    except BrowserLaunchError as exc:
        if telemetry_run_id:
            try:
                telemetry.finish_run(
                    telemetry_run_id,
                    status="browser_launch_error",
                    completed_count=0,
                    search_attempt_count=0,
                )
            except Exception:
                pass
        logger.error("Browser launch failed: %s", exc)
        conn.close()
        raise

    logger.info(
        "Start: selected=%d pending=%d run_hours=%.2f interval=%.2fs",
        len(tasks), len(pending), cfg.run_hours, interval
    )
    t0 = time.monotonic()
    stop_run = False
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
    process_exit_code = 0
    completed_count = 0
    previous_search_started = None
    final_status = "running"

    try:
        for idx, task in enumerate(pending):
            scheduled = t0 + idx * interval
            delay = scheduled - time.monotonic()
            if delay > 0:
                time.sleep(delay)

            search_started = time.monotonic()
            actual_start_gap_ms = (
                round((search_started - previous_search_started) * 1000)
                if previous_search_started is not None
                else None
            )
            previous_search_started = search_started
            if telemetry_run_id:
                try:
                    telemetry.update_attempt_count(telemetry_run_id, idx + 1)
                except Exception as exc:
                    logger.warning("Telemetry update failed: %s", type(exc).__name__)
            logger.info("[%d/%d] SEARCH %s | target=%s", idx+1, len(pending), task.keyword, task.target_domain)
            items = []
            try:
                google_url, items, elapsed = browser.search(task.keyword, cfg.max_results)
                decision = decide_source_rank(
                    browser.last_source_observations,
                    task.target_domain,
                    cfg.max_results,
                    cfg.include_subdomains,
                )
                result = SearchResult(
                    task.keyword,
                    task.target_domain,
                    decision.result_code,
                    describe(decision.result_code),
                    matched_rank=decision.matched_rank,
                    matched_url=decision.matched_url,
                    collected_count=len(items),
                    google_url=google_url,
                    elapsed_ms=elapsed,
                    message=decision.message,
                )
                logger.info(
                    "[%d/%d] stages=%s",
                    idx + 1,
                    len(pending),
                    format_search_metrics(browser.last_search_metrics) or "no measurable delay",
                )

            except ChallengeDetected as exc:
                if telemetry_run_id:
                    try:
                        network = profile_network_snapshot(cfg.log_dir.parent, cfg.cdp_endpoint)
                        ordinal = telemetry.record_human_verification(
                            run_id=telemetry_run_id,
                            event_type="challenge",
                            task_index=idx + 1,
                            search_sequence_number=idx + 1,
                            task_attempt_number=1,
                            completed_before=completed_count,
                            actual_start_gap_ms=actual_start_gap_ms,
                            configured_delay_seconds=interval,
                            **network,
                        )
                        logger.warning("[%d/%d] verification_ordinal=%d", idx + 1, len(pending), ordinal)
                    except Exception as telemetry_exc:
                        logger.warning("Telemetry event failed: %s", type(telemetry_exc).__name__)
                diag = browser.save_diagnostics(cfg.log_dir / "diagnostics", f"challenge_{run_date}_{idx+1:04d}")
                msg = str(exc) + (f" | diagnostics={'; '.join(diag)}" if diag else "")
                result = SearchResult(task.keyword, task.target_domain, -4, describe(-4), message=msg)
                stop_run = True
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                process_exit_code = 4
            except ConsentRequired as exc:
                if telemetry_run_id:
                    try:
                        network = profile_network_snapshot(cfg.log_dir.parent, cfg.cdp_endpoint)
                        ordinal = telemetry.record_human_verification(
                            run_id=telemetry_run_id,
                            event_type="consent",
                            task_index=idx + 1,
                            search_sequence_number=idx + 1,
                            task_attempt_number=1,
                            completed_before=completed_count,
                            actual_start_gap_ms=actual_start_gap_ms,
                            configured_delay_seconds=interval,
                            **network,
                        )
                        logger.warning("[%d/%d] verification_ordinal=%d", idx + 1, len(pending), ordinal)
                    except Exception as telemetry_exc:
                        logger.warning("Telemetry event failed: %s", type(telemetry_exc).__name__)
                diag = browser.save_diagnostics(cfg.log_dir / "diagnostics", f"consent_{run_date}_{idx+1:04d}")
                msg = str(exc) + (f" | diagnostics={'; '.join(diag)}" if diag else "")
                result = SearchResult(task.keyword, task.target_domain, -9, describe(-9), message=msg)
                stop_run = True
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                process_exit_code = 9
            except NavigationError as exc:
                result = SearchResult(task.keyword, task.target_domain, -2, describe(-2), message=str(exc))
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                process_exit_code = process_exit_code or 2
            except SearchParseTimeout as exc:
                result = SearchResult(task.keyword, task.target_domain, -10, describe(-10), message=str(exc))
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                process_exit_code = process_exit_code or 2
            except Exception as exc:
                result = SearchResult(task.keyword, task.target_domain, -8, describe(-8), message=repr(exc))
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                process_exit_code = process_exit_code or 2

# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
            if result.result_code <= -2 and process_exit_code == 0:
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                process_exit_code = 2

            save_result(conn, run_date, result, items)
            completed_count = idx + 1
            export_csv(conn, run_date, export_path)
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
            logger.info("[%d/%d] code=%d type=%s count=%d", idx+1, len(pending), result.result_code, result.result_type, result.collected_count)

            if result.message:
                logger.info("[%d/%d] detail=%s", idx+1, len(pending), result.message)

            if stop_run:
# 状态码用于区分成功、网络异常、Google Challenge等不同结果。
                final_status = "challenge" if result.result_code == -4 else "consent"
                logger.error("Google challenge/consent state detected. Run stopped; no bypass attempt will be made.")
                break
        if final_status == "running":
            final_status = "complete"
    finally:
        if telemetry_run_id:
            try:
                telemetry.finish_run(
                    telemetry_run_id,
                    status=final_status,
                    completed_count=completed_count,
                    search_attempt_count=completed_count,
                )
            except Exception as exc:
                logger.warning("Telemetry finish failed: %s", type(exc).__name__)
        export_csv(conn, run_date, export_path)
        browser.close()
        conn.close()
# port表示网络端口，用于区分同一主机上的不同服务入口。
        logger.info("Finished. Output=%s", export_path)
    return process_exit_code
