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
)

# Try to import tree_sitter.Parser to construct per-thread parsers.
# If tree_sitter isn't available at import time, we'll still work but fall back to a global parser + lock.
try:
    from tree_sitter import Parser as TS_Parser  # type: ignore
except Exception:
    TS_Parser = None  # type: ignore

# ------------------------------
# Logging setup
# ------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

# ------------------------------
# Parser fallback lock (used only if we must share a parser)
# ------------------------------
_PARSE_LOCK = threading.Lock()


def is_source_file(filename: str) -> bool:
    return filename.endswith(".c") or filename.endswith(".h")


def get_function_snippet(filepath: str, start_line: int, end_line: int) -> str:
    """
    Return the snippet for a function. `start_line` and `end_line` are 1-based,
    and `end_line` is treated as inclusive.
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    # Guard against out-of-range indices
    start_index = max(0, start_line - 1)
    # end_line is inclusive, but python slice end is exclusive
    end_index = min(len(lines), end_line)
    return "".join(lines[start_index:end_index])


class _DBWriter(threading.Thread):
    """
    Single writer thread that consumes tasks from a queue and performs all DB writes
    using the provided helper functions. This serializes writes and avoids SQLite
    'database is locked' issues when many parser threads finish around the same time.
    """

    def __init__(self, db_pool: SQLiteConnectionPool):
        super().__init__(daemon=True)
        self._db_pool = db_pool
        self._q: "Queue[Optional[Tuple[str, str, dict]]]" = Queue()
        self._stopped = threading.Event()

    def enqueue(self, root_path: str, rel_path: str, info: dict):
        """
        Enqueue a (root_path, rel_path, info) tuple for writing.
        """
        self._q.put((root_path, rel_path, info))

    def stop(self):
        """
        Signal the writer to finish and exit after processing queued tasks.
        """
        self._q.put(None)  # sentinel

    def run(self):
        logger.info("DB writer thread started.")
        while True:
            item = self._q.get()
            if item is None:
                # sentinel: finish
                logger.info("DB writer received sentinel, finishing.")
                break
            try:
                root_path, rel_path, info = item
                try:
                    store_file_info(self._db_pool, root_path, rel_path, info)
                except Exception:
                    logger.exception("DB writer failed storing file %s", rel_path)
            finally:
                self._q.task_done()
        logger.info("DB writer thread exiting.")


def parse_and_store_entire_codebase(
    db_pool: SQLiteConnectionPool,
    parser_or_language: Any,
    root_path: str,
    max_workers: Optional[int] = None,
):
    """
    Threaded file parsing + DB storing with a dedicated writer thread to serialize DB writes.
    Worker threads focus on parsing (concurrent). Parsed results are enqueued to the writer.
    """
    root_path = os.path.abspath(root_path)
    logger.info(f"Starting full codebase parse at: {root_path}")

    # Collect files first
    all_files: List[Tuple[str, str]] = []
    for dirpath, _, filenames in os.walk(root_path):
        for fname in filenames:
            if is_source_file(fname):
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, root_path)
                all_files.append((full_path, rel_path))

    total = len(all_files)
    logger.info(f"Discovered {total} source files to process.")
    if total == 0:
        logger.info("No source files found — nothing to do.")
        return

    # Decide max_workers
    if max_workers is None:
        try:
            max_workers = min(32, (os.cpu_count() or 4) * 2)
        except Exception:
            max_workers = 8
    max_workers = max(1, int(max_workers))
    logger.info(f"Processing files with up to {max_workers} worker threads.")

    # Shared state for progress tracking
    processed_count = 0
    processed_count_lock = threading.Lock()
    last_processed_relpath = {"path": None}
    last_processed_lock = threading.Lock()
    done_event = threading.Event()

    # Determine whether we can create per-thread Parsers (we need a Language object)
    language_for_workers = None
    can_create_per_thread_parser = False
    if parser_or_language is None:
        language_for_workers = None
        can_create_per_thread_parser = False
    else:
        # If user passed a Parser instance, try to extract its language attribute
        if TS_Parser is not None and isinstance(parser_or_language, TS_Parser):
            # parser_or_language is an existing Parser instance: try to get its language
            language_for_workers = getattr(
                parser_or_language, "language", None
            ) or getattr(parser_or_language, "_language", None)
            can_create_per_thread_parser = language_for_workers is not None
        else:
            # Might be a Language object (or something with .language attr)
            language_for_workers = (
                getattr(parser_or_language, "language", None) or parser_or_language
            )
            if TS_Parser is not None and language_for_workers is not None:
                can_create_per_thread_parser = True

    if can_create_per_thread_parser:
        logger.info(
            "Parallel parsing: will instantiate a Parser per worker and call set_language()."
        )
    else:
        logger.warning(
            "Parallel parsing not available; parsing will be serialized with a lock (safer but slower)."
        )

    # Start the DB writer thread
    writer = _DBWriter(db_pool)
    writer.start()

    def _process_file(full_path: str, rel_path: str):
        nonlocal processed_count
        # Mark this as current file immediately so watchdog sees it
        with last_processed_lock:
            last_processed_relpath["path"] = rel_path
        logger.debug(f"[submit] {rel_path} — worker picked up task")

        try:
            # === parse step ===
            logger.debug(f"[parse-start] {rel_path}")
            local_parser = None
            need_lock = True

            if (
                can_create_per_thread_parser
                and TS_Parser is not None
                and language_for_workers is not None
            ):
                try:
                    # Correct: create Parser() and then set_language(language)
                    local_parser = TS_Parser()
                    if hasattr(local_parser, "set_language"):
                        local_parser.set_language(language_for_workers)
                        need_lock = False
                    else:
                        # Unexpected: cannot set language; fall back to lock
                        local_parser = None
                        need_lock = True
                except Exception:
                    logger.exception(
                        "[parse] failed to create or configure local Parser, falling back to lock"
                    )
                    local_parser = None
                    need_lock = True

            if need_lock:
                with _PARSE_LOCK:
                    # call extract_info using shared parser_or_language or a temp parser
                    if TS_Parser is not None and isinstance(
                        parser_or_language, TS_Parser
                    ):
                        info = extract_info_from_file(parser_or_language, full_path)
                    elif TS_Parser is not None and language_for_workers is not None:
                        tmp_parser = TS_Parser()
                        try:
                            if hasattr(tmp_parser, "set_language"):
                                tmp_parser.set_language(language_for_workers)
                            info = extract_info_from_file(tmp_parser, full_path)
                        finally:
                            # let GC handle tmp_parser
                            pass
                    else:
                        # Non-tree-sitter extraction (fallback)
                        info = extract_info_from_file(parser_or_language, full_path)
            else:
                # Use the per-thread parser
                info = extract_info_from_file(local_parser, full_path)
            logger.debug(f"[parse-done] {rel_path}")

            # === enqueue store step ===
            logger.debug(f"[enqueue-store] {rel_path}")
            writer.enqueue(root_path, rel_path, info)
            logger.debug(f"[enqueued] {rel_path}")

            # update processed counter (parsing stage complete; note: write may still be pending)
            with processed_count_lock:
                processed_count += 1
            logger.debug(f"[done-parse] {rel_path} ({processed_count}/{total})")

            # clear current file marker
            with last_processed_lock:
                last_processed_relpath["path"] = None

            return True, rel_path, None
        except Exception as e:
            logger.exception(f"[error] {rel_path}: {e}")
            # make sure we still update counters so watchdog knows progress halted on this file
            with processed_count_lock:
                processed_count += 1
            with last_processed_lock:
                last_processed_relpath["path"] = None
            return False, rel_path, e

    # Watchdog thread: reports progress every N seconds until done_event is set
    def _watchdog(interval: float = 5.0):
        logger.info("Watchdog started: reporting progress every %.1fs", interval)
        while not done_event.is_set():
            with processed_count_lock:
                p = processed_count
            with last_processed_lock:
                cur = last_processed_relpath["path"]
            logger.info("Progress: %d/%d parsed. current=%s", p, total, cur or "(idle)")
            done_event.wait(interval)
        logger.info("Watchdog stopping.")

    # Start watchdog
    wd = threading.Thread(target=_watchdog, daemon=True)
    wd.start()

    # Submit work
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_process_file, fp, rp) for fp, rp in all_files]

        # as_completed will yield when futures finish
        for fut in as_completed(futures):
            try:
                success, rel_path, exc = fut.result()
                # result handling already done in worker, so nothing heavy here
            except Exception as e:
                logger.exception("Unexpected exception retrieving future result: %s", e)

    # All done parsing — signal writer to stop after flushing queue
    done_event.set()
    wd.join(timeout=2.0)

    # Ensure all enqueued write tasks are processed, then stop writer
    logger.info("Waiting for DB writer to finish queued writes...")
    writer.stop()
    writer.join(timeout=30.0)
    logger.info("All worker tasks and DB writes finished.")


def store_file_info(
    db_pool: SQLiteConnectionPool, root_path: str, rel_path: str, info: dict
):
    """
    Store symbols, typedefs, globals, functions, and function-calls for one file.

    NOTE: This function runs in the single DB writer thread (so it is safe to call
    helper functions that themselves manage connections). We avoid acquiring a
    connection here and instead rely on the helper functions to do the right thing.
    """
    logger.debug(f"Storing symbols/functions for file: {rel_path}")

    # Ensure we have a file_id (helper will manage connections)
    file_id = insert_or_get_file_id(db_pool, rel_path)
    logger.debug(f"File ID for {rel_path}: {file_id}")

    # Insert structs
    for s in info.get("structs", []):
        logger.debug(f"Inserting struct: {s.get('name')}")
        insert_file_symbol(db_pool, file_id, "struct", s.get("name"), s.get("code"))

    # Insert typedefs
    for t in info.get("typedefs", []):
        logger.debug(f"Inserting typedef: {t.get('alias')}")
        insert_file_symbol(db_pool, file_id, "typedef", t.get("alias"), t.get("code"))

    # Insert globals
    for g in info.get("globals", []):
        logger.debug(f"Inserting global: {g.get('name')}")
        insert_file_symbol(db_pool, file_id, "global", g.get("name"), g.get("type"))

    # Insert functions
    abs_file_path = os.path.join(root_path, rel_path)
    all_funcs = info.get("functions", [])
    for func in all_funcs:
        snippet = get_function_snippet(
            abs_file_path, func["start_line"], func["end_line"]
        )
        hash_ = compute_code_hash(snippet)
        logger.debug(
            f"Inserting function: {func.get('name')} ({func.get('start_line')}-{func.get('end_line')})"
        )
        insert_function(
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

    # Insert function calls
    for caller_line, callee_name in info.get("calls", []):
        caller_func_id = None
        for func in all_funcs:
            if func["start_line"] <= caller_line <= func["end_line"]:
                caller_func_id = _find_function_id(db_pool, file_id, func["name"])
                break
        if caller_func_id:
            callee_func_id = _find_function_id(db_pool, None, callee_name)
            if callee_func_id:
                logger.debug(
                    f"Inserting function call: {caller_func_id} -> {callee_func_id}"
                )
                insert_function_call(db_pool, caller_func_id, callee_func_id)


def _find_function_id(
    db_pool: SQLiteConnectionPool, file_id: Optional[int], func_name: str
) -> Optional[int]:
    """
    Helper that queries functions table. The helper will acquire/release a connection.
    """
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
            # Global search: prefer real definitions
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
