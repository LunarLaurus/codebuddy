# actions/run_analysis.py
import asyncio
import logging
from pathlib import Path
import threading

from util.db_utils import SQLiteConnectionPool
from code_analysis.parser import load_language, get_parser
from code_analysis.code_map_builder import parse_and_store_entire_codebase
from code_analysis.summarization import resummarize_file, summarize_function_in_db
from code_analysis.code_map import build_code_map_from_db as _build_code_map_from_db
from code_analysis.output import print_code_map

# ------------------------------
# Logging setup
# ------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# Lock to serialize DB access in this module
_db_lock = threading.Lock()


async def _run_full_analysis_async(
    repo_path: str, db_path: str, summarize_functions: bool
):
    logger.info("Initializing DB connection pool...")
    db_pool = SQLiteConnectionPool(db_path, pool_size=5, timeout=30, enable_wal=True)

    logger.info("Loading parser for C language...")
    language = load_language("c")
    parser = get_parser(language)
    root_path = Path(repo_path).resolve()
    logger.info(f"Resolved repository path: {root_path}")

    logger.info("Parsing and storing entire codebase...")
    # Run parse_and_store_entire_codebase in a separate thread and wait for DB writer to finish
    await asyncio.to_thread(
        parse_and_store_entire_codebase, db_pool, parser, str(root_path)
    )
    logger.info("Codebase parsing complete.")

    # File-level summaries
    with _db_lock:
        conn = db_pool.acquire()
        try:
            cur = conn.cursor()
            cur.execute("SELECT file_id, path FROM files")
            files = cur.fetchall()
            logger.info(f"Found {len(files)} files to summarize.")
        finally:
            db_pool.release(conn)

    if files:
        logger.info("Starting file-level summaries...")
        tasks = [
            asyncio.to_thread(
                resummarize_file,
                db_pool,
                parser,
                root_path / rel_path,
                commit_sha="HEAD",
            )
            for _, rel_path in files
        ]
        await asyncio.gather(*tasks)
        logger.info("File-level summaries complete.")

    # Function-level summaries
    if summarize_functions:
        logger.info("Starting function-level summaries...")
        with _db_lock:
            conn = db_pool.acquire()
            try:
                cur = conn.cursor()
                cur.execute("SELECT function_id, code_snippet FROM functions")
                functions = cur.fetchall()
                logger.info(f"Found {len(functions)} functions to summarize.")
            finally:
                db_pool.release(conn)

        func_tasks = [
            asyncio.to_thread(
                summarize_function_in_db, db_pool, fid, snippet, commit_sha="HEAD"
            )
            for fid, snippet in functions
            if snippet.strip()
        ]
        if func_tasks:
            await asyncio.gather(*func_tasks)
        logger.info("Function-level summaries complete.")

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
