# code_analysis/code_map_builder.py

import os
import json
import logging
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from queue import Queue
from typing import Any, List, Tuple, Optional

from code_analysis.code_extractor import extract_info_from_file
from util.db_utils import (
    SQLiteConnectionPool,
    insert_or_get_file_id,
    insert_file_symbol,
    insert_function,
    insert_function_call,
    compute_code_hash,
    mark_processed,
    is_processed,
)

# Optional Tree-sitter import
try:
    from tree_sitter import Parser as TS_Parser  # type: ignore
except Exception:
    TS_Parser = None  # type: ignore

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

_PARSE_LOCK = threading.Lock()


def is_source_file(filename: str) -> bool:
    return filename.endswith(".c") or filename.endswith(".h")


def get_function_snippet(filepath: str, start_line: int, end_line: int) -> str:
    """Return snippet of a function (1-based, inclusive end)."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    start_index = max(0, start_line - 1)
    end_index = min(len(lines), end_line)
    return "".join(lines[start_index:end_index])


class _DBWriter(threading.Thread):
    """Single DB writer with incremental flush to avoid memory accumulation."""

    def __init__(self, db_pool: SQLiteConnectionPool, flush_interval: float = 0.5):
        super().__init__(daemon=True)
        self._db_pool = db_pool
        self._q: "Queue[Optional[Tuple[str, str, dict]]]" = Queue()
        self._stopped = threading.Event()
        self._flush_interval = flush_interval

    def enqueue(self, root_path: str, rel_path: str, info: dict):
        self._q.put((root_path, rel_path, info))

    def stop(self):
        self._stopped.set()

    def run(self):
        logger.info("DB writer thread started.")
        while not self._stopped.is_set() or not self._q.empty():
            try:
                item = self._q.get(timeout=self._flush_interval)
            except Empty:
                continue

            if item is None:
                continue

            root_path, rel_path, info = item
            try:
                store_file_info(self._db_pool, root_path, rel_path, info)
            except Exception:
                logger.exception("DB writer failed storing file %s", rel_path)
            finally:
                self._q.task_done()
        logger.info("DB writer thread exiting.")


class _DBWriterSerial(threading.Thread):
    """Single DB writer thread to serialize writes."""

    def __init__(self, db_pool: SQLiteConnectionPool):
        super().__init__(daemon=True)
        self._db_pool = db_pool
        self._q: "Queue[Optional[Tuple[str, str, dict]]]" = Queue()

    def enqueue(self, root_path: str, rel_path: str, info: dict):
        self._q.put((root_path, rel_path, info))

    def stop(self):
        self._q.put(None)

    def run(self):
        logger.info("DB writer thread started.")
        while True:
            item = self._q.get()
            if item is None:
                break
            root_path, rel_path, info = item
            try:
                store_file_info(self._db_pool, root_path, rel_path, info)
            except Exception:
                logger.exception("DB writer failed storing file %s", rel_path)
            finally:
                self._q.task_done()
        logger.info("DB writer thread exiting.")


def store_file_info(
    db_pool: SQLiteConnectionPool, root_path: str, rel_path: str, info: dict
):
    """Store file info and mark file/functions processed."""
    file_id = insert_or_get_file_id(db_pool, rel_path)

    # Skip already processed files
    if is_processed(db_pool, "file", file_id, commit_sha="HEAD"):
        logger.debug(f"Skipping already processed file {rel_path}")
        return

    # Structs
    for s in info.get("structs", []):
        insert_file_symbol(db_pool, file_id, "struct", s.get("name"), s.get("code"))

    # Typedefs
    for t in info.get("typedefs", []):
        insert_file_symbol(db_pool, file_id, "typedef", t.get("alias"), t.get("code"))

    # Globals
    for g in info.get("globals", []):
        insert_file_symbol(db_pool, file_id, "global", g.get("name"), g.get("type"))

    # Functions
    abs_file_path = os.path.join(root_path, rel_path)
    all_funcs = info.get("functions", [])
    for func in all_funcs:
        snippet = get_function_snippet(
            abs_file_path, func["start_line"], func["end_line"]
        )
        hash_ = compute_code_hash(snippet)
        func_id = insert_function(
            db_pool,
            file_id,
            func.get("name") or "",
            func.get("return_type") or "",
            json.dumps(func.get("parameters", [])),
            func.get("start_line"),
            func.get("end_line"),
            func.get("prototype", False),
            hash_,
            snippet,
        )
        if not is_processed(db_pool, "function", func_id, commit_sha="HEAD"):
            mark_processed(db_pool, "function", func_id, commit_sha="HEAD")

    # Function calls
    for caller_line, callee_name in info.get("calls", []):
        caller_func_id = None
        for func in all_funcs:
            if func["start_line"] <= caller_line <= func["end_line"]:
                caller_func_id = _find_function_id(db_pool, file_id, func["name"])
                break
        if caller_func_id:
            callee_func_id = _find_function_id(db_pool, None, callee_name)
            if callee_func_id:
                insert_function_call(db_pool, caller_func_id, callee_func_id)

    mark_processed(db_pool, "file", file_id, commit_sha="HEAD")


def _find_function_id(
    db_pool: SQLiteConnectionPool, file_id: Optional[int], func_name: str
) -> Optional[int]:
    conn = db_pool.acquire()
    try:
        cur = conn.cursor()
        if file_id is not None:
            cur.execute(
                "SELECT function_id FROM functions WHERE file_id=? AND name=? LIMIT 1",
                (file_id, func_name),
            )
            row = cur.fetchone()
            return row[0] if row else None
        else:
            cur.execute(
                "SELECT function_id FROM functions WHERE name=? AND is_prototype=0 LIMIT 1",
                (func_name,),
            )
            row = cur.fetchone()
            if row:
                return row[0]
            cur.execute(
                "SELECT function_id FROM functions WHERE name=? LIMIT 1", (func_name,)
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        db_pool.release(conn)


def parse_and_store_entire_codebase(
    db_pool: SQLiteConnectionPool,
    parser_or_language: Any,
    root_path: str,
    max_workers: Optional[int] = None,
):
    """Parse codebase concurrently, enqueue results to DB writer."""
    root_path = os.path.abspath(root_path)
    logger.info(f"Starting full codebase parse at: {root_path}")

    # Collect source files
    all_files: List[Tuple[str, str]] = [
        (os.path.join(dp, f), os.path.relpath(os.path.join(dp, f), root_path))
        for dp, _, files in os.walk(root_path)
        for f in files
        if is_source_file(f)
    ]
    total = len(all_files)
    if total == 0:
        logger.info("No source files found — nothing to do.")
        return
    logger.info(f"Discovered {total} source files to process.")

    if max_workers is None:
        max_workers = max(1, min(32, (os.cpu_count() or 4) * 2))
    logger.info(f"Processing files with up to {max_workers} worker threads.")

    processed_count = 0
    processed_lock = threading.Lock()
    current_file = {"path": None}
    current_lock = threading.Lock()
    done_event = threading.Event()

    # Parser per-thread check
    language_for_workers = None
    can_create_per_thread_parser = False
    if TS_Parser is not None and parser_or_language is not None:
        if isinstance(parser_or_language, TS_Parser):
            language_for_workers = getattr(parser_or_language, "language", None)
        else:
            language_for_workers = (
                getattr(parser_or_language, "language", None) or parser_or_language
            )
        can_create_per_thread_parser = language_for_workers is not None

    # Start DB writer
    writer = _DBWriter(db_pool)
    writer.start()

    def _process_file(full_path: str, rel_path: str):
        nonlocal processed_count
        with current_lock:
            current_file["path"] = rel_path

        # Skip already processed files
        file_id = insert_or_get_file_id(db_pool, rel_path)
        if is_processed(db_pool, "file", file_id, commit_sha="HEAD"):
            logger.debug(f"Skipping already processed file {rel_path}")
            with processed_lock:
                processed_count += 1
            return

        try:
            # Build per-thread parser if available
            local_parser = None
            need_lock = True
            if can_create_per_thread_parser:
                try:
                    local_parser = TS_Parser()
                    if hasattr(local_parser, "set_language"):
                        local_parser.set_language(language_for_workers)
                        need_lock = False
                except Exception:
                    local_parser = None
                    need_lock = True

            if need_lock:
                with _PARSE_LOCK:
                    if TS_Parser and isinstance(parser_or_language, TS_Parser):
                        info = extract_info_from_file(parser_or_language, full_path)
                    else:
                        tmp_parser = TS_Parser() if TS_Parser else None
                        if tmp_parser and hasattr(tmp_parser, "set_language"):
                            tmp_parser.set_language(language_for_workers)
                        info = extract_info_from_file(
                            tmp_parser or parser_or_language, full_path
                        )
            else:
                info = extract_info_from_file(local_parser, full_path)

            writer.enqueue(root_path, rel_path, info)

        except Exception as e:
            logger.exception("Failed parsing file %s: %s", rel_path, e)
        finally:
            with processed_lock:
                processed_count += 1
            with current_lock:
                current_file["path"] = None

    # Watchdog for progress
    def _watchdog(interval: float = 2.0):
        while not done_event.is_set():
            with processed_lock, current_lock:
                logger.info(
                    "Progress: %d/%d parsed. current=%s",
                    processed_count,
                    total,
                    current_file["path"] or "(idle)",
                )
            done_event.wait(interval)

    wd = threading.Thread(target=_watchdog, daemon=True)
    wd.start()

    # Thread pool
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_process_file, fp, rp) for fp, rp in all_files]
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception as e:
                logger.exception("Unexpected error: %s", e)

    # Finish
    done_event.set()
    wd.join(timeout=2.0)
    writer.stop()
    writer.join(timeout=30.0)
    logger.info("All files parsed and DB writes completed.")
