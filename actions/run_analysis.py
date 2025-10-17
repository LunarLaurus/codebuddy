# actions/run_analysis.py

import asyncio
import logging
from pathlib import Path
import signal

from util.db_utils import (
    SQLiteConnectionPool,
    get_unprocessed_files,
    get_unprocessed_functions,
    mark_processed,
    update_database_schema,
    is_processed,
)
from code_analysis.parser import load_language, get_parser
from code_analysis.code_map_builder import parse_and_store_entire_codebase
from code_analysis.code_map import build_code_map_from_db as _build_code_map_from_db
from util.llm_client import set_mode_c, set_mode_file
from .resummarize import _resummarize_file_async, summarize_function_in_db_async

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

_stop_flag = False


def _handle_sigint(signum, frame):
    global _stop_flag
    _stop_flag = True
    logger.info("Received interrupt signal, will stop after current tasks.")


signal.signal(signal.SIGINT, _handle_sigint)
signal.signal(signal.SIGTERM, _handle_sigint)


async def _run_full_analysis_async(
    repo_path: str, db_path: str, summarize_functions: bool, max_workers: int = 10
):
    """
    1. Parse & store entire codebase using threaded parser (non-async).
    2. Run file-level summaries concurrently (awaiting coroutine directly).
    3. Optionally run function-level summaries concurrently.
    """

    global _stop_flag

    # Setup DB pool and parser
    update_database_schema(db_path)
    db_pool = SQLiteConnectionPool(
        db_path, pool_size=max_workers, timeout=30, enable_wal=True
    )

    language = load_language("c")
    parser = get_parser(language)
    root_path = Path(repo_path).resolve()
    logger.info(f"Resolved repository path: {root_path}")

    # --- 1) Parse & store entire codebase (runs in threads) ---
    logger.info("Parsing and storing entire codebase...")
    # parse_and_store_entire_codebase is synchronous and thread-based, so run in a thread
    await asyncio.to_thread(
        parse_and_store_entire_codebase, db_pool, parser, str(root_path), max_workers
    )
    logger.info("Codebase parsing complete.")

    # --- 2) File-level summaries (async coroutines, awaited directly) ---
    files = get_unprocessed_files(db_pool)
    logger.info(f"Found {len(files)} files to summarize.")
    set_mode_file()

    file_semaphore = asyncio.Semaphore(max_workers)

    async def summarize_file_task(file_id: int, rel_path: str):
        """Wrap the coroutine call with semaphore, stop-checking, logging and marking."""
        if _stop_flag:
            logger.debug("Stop flag set; skipping file summary %s", rel_path)
            return
        # double-check incremental state
        if is_processed(db_pool, "file", file_id, commit_sha="HEAD"):
            logger.debug(
                "Already summarized file %s (id=%s); skipping", rel_path, file_id
            )
            return

        async with file_semaphore:
            try:
                logger.debug("Starting summary for %s (id=%s)", rel_path, file_id)
                # _resummarize_file_async is a coroutine: await it directly
                await _resummarize_file_async(
                    db_pool, parser, root_path / rel_path, commit_sha="HEAD"
                )
                # mark processed only on success
                mark_processed(db_pool, "file", file_id, commit_sha="HEAD")
                logger.info("Summarized and marked file %s (id=%s)", rel_path, file_id)
            except Exception:
                logger.exception(
                    "Failed to summarize file %s (id=%s)", rel_path, file_id
                )

    # create tasks but do not await inline to allow concurrency up to semaphore limit
    file_tasks = [
        asyncio.create_task(summarize_file_task(fid, rel)) for fid, rel in files
    ]

    if file_tasks:
        # gather and wait for completion, but allow cancellations/stop-flag to cancel
        try:
            await asyncio.gather(*file_tasks)
        except asyncio.CancelledError:
            logger.warning("File summary tasks cancelled.")
    logger.info("File-level summaries complete.")

    # --- 3) Function-level summaries (if requested) ---
    if summarize_functions:
        functions = get_unprocessed_functions(db_pool)
        logger.info(f"Found {len(functions)} functions to summarize.")
        set_mode_c()

        func_semaphore = asyncio.Semaphore(max_workers)

        async def summarize_function_task(fid: int, snippet: str):
            if _stop_flag:
                return
            if not snippet or not snippet.strip():
                return
            if is_processed(db_pool, "function", fid, commit_sha="HEAD"):
                return

            async with func_semaphore:
                try:
                    await summarize_function_in_db_async(
                        db_pool, fid, snippet, commit_sha="HEAD"
                    )
                    mark_processed(db_pool, "function", fid, commit_sha="HEAD")
                    logger.info("Summarized and marked function id=%s", fid)
                except Exception:
                    logger.exception("Failed to summarize function id=%s", fid)

        func_tasks = [
            asyncio.create_task(summarize_function_task(fid, snippet))
            for fid, snippet in functions
        ]

        if func_tasks:
            try:
                await asyncio.gather(*func_tasks)
            except asyncio.CancelledError:
                logger.warning("Function summary tasks cancelled.")
        logger.info("Function-level summaries complete.")

    # --- 4) Build code map and finish ---
    logger.info("Building code map from DB...")
    code_map = _build_code_map_from_db(db_pool)
    logger.info("Code map construction complete.")
    return code_map


def run_full_analysis(repo_path: str, db_path: str, summarize_functions: bool = False):
    """Synchronous wrapper for menu."""
    logger.info("Running full analysis...")
    result = asyncio.run(
        _run_full_analysis_async(repo_path, db_path, summarize_functions)
    )
    logger.info("Full analysis finished.")
    return result


def build_code_map_from_db(db_path_or_pool):
    """Flexible: accept either DB path or connection pool."""
    if isinstance(db_path_or_pool, str):
        db_pool = SQLiteConnectionPool(
            db_path_or_pool, pool_size=10, timeout=30, enable_wal=True
        )
    else:
        db_pool = db_path_or_pool
    return _build_code_map_from_db(db_pool)
