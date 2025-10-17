# actions/run_analysis.py

import asyncio
import logging
from pathlib import Path
import threading
import signal

from util.db_utils import (
    SQLiteConnectionPool,
    mark_processed,
)
from code_analysis.parser import load_language, get_parser
from code_analysis.code_map_builder import parse_and_store_entire_codebase
from .resummarize import _resummarize_file_async, summarize_function_in_db
from code_analysis.code_map import build_code_map_from_db as _build_code_map_from_db

# ------------------------------
# Logging setup
# ------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Lock to serialize DB access in this module
_db_lock = threading.Lock()

# Global stop flag for graceful shutdown
_stop_flag = False


def _handle_sigint(signum, frame):
    global _stop_flag
    _stop_flag = True
    logger.info("Received interrupt signal, will stop after current tasks.")


signal.signal(signal.SIGINT, _handle_sigint)
signal.signal(signal.SIGTERM, _handle_sigint)


def _get_unprocessed_files(db_pool, commit_sha="HEAD"):
    """Return list of files not yet summarized for this commit."""
    with _db_lock:
        conn = db_pool.acquire()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT f.file_id, f.path
                FROM files f
                LEFT JOIN summary_status_commit s
                    ON s.item_type='file' AND s.item_id=f.file_id AND s.commit_sha=?
                WHERE s.history_id IS NULL
                """,
                (commit_sha,),
            )
            return cur.fetchall()
        finally:
            db_pool.release(conn)


def _get_unprocessed_functions(db_pool, commit_sha="HEAD"):
    """Return list of functions not yet summarized for this commit."""
    with _db_lock:
        conn = db_pool.acquire()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT fn.function_id, fn.code_snippet
                FROM functions fn
                LEFT JOIN summary_status_commit s
                    ON s.item_type='function' AND s.item_id=fn.function_id AND s.commit_sha=?
                WHERE s.history_id IS NULL
                """,
                (commit_sha,),
            )
            return cur.fetchall()
        finally:
            db_pool.release(conn)


async def _run_full_analysis_async(
    repo_path: str, db_path: str, summarize_functions: bool
):
    """Parse, summarize, and store the entire codebase asynchronously."""
    global _stop_flag

    logger.info("Initializing DB connection pool...")
    db_pool = SQLiteConnectionPool(db_path, pool_size=5, timeout=30, enable_wal=True)

    logger.info("Loading parser for C language...")
    language = load_language("c")
    parser = get_parser(language)
    root_path = Path(repo_path).resolve()
    logger.info(f"Resolved repository path: {root_path}")

    logger.info("Parsing and storing entire codebase...")
    await asyncio.to_thread(
        parse_and_store_entire_codebase, db_pool, parser, str(root_path)
    )
    logger.info("Codebase parsing complete.")

    # ---------------- File-level summaries ----------------
    files = _get_unprocessed_files(db_pool)
    logger.info(f"Found {len(files)} files to summarize.")

    for file_id, rel_path in files:
        if _stop_flag:
            logger.info("Stopping file-level summaries due to interrupt.")
            break
        await asyncio.to_thread(
            _resummarize_file_async,
            db_pool,
            parser,
            root_path / rel_path,
            commit_sha="HEAD",
        )
        mark_processed(db_pool, "file", file_id, commit_sha="HEAD")

    logger.info("File-level summaries complete.")

    # ---------------- Function-level summaries ----------------
    if summarize_functions:
        functions = _get_unprocessed_functions(db_pool)
        logger.info(f"Found {len(functions)} functions to summarize.")

        for fid, snippet in functions:
            if _stop_flag:
                logger.info("Stopping function-level summaries due to interrupt.")
                break
            if not snippet.strip():
                continue
            await asyncio.to_thread(
                summarize_function_in_db, db_pool, fid, snippet, commit_sha="HEAD"
            )
            mark_processed(db_pool, "function", fid, commit_sha="HEAD")

        logger.info("Function-level summaries complete.")

    # ---------------- Build code map ----------------
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
        logger.info(f"Initializing DB pool for path: {db_path_or_pool}")
        db_pool = SQLiteConnectionPool(
            db_path_or_pool, pool_size=5, timeout=30, enable_wal=True
        )
    else:
        db_pool = db_path_or_pool
    return _build_code_map_from_db(db_pool)
