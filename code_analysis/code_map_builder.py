# code_analysis/code_map_builder.py
import os
import json
import logging
from pathlib import Path
from code_analysis.code_extractor import extract_info_from_file
from util.db_utils import (
    SQLiteConnectionPool,
    insert_or_get_file_id,
    insert_file_symbol,
    insert_function,
    insert_function_call,
    compute_code_hash,
)

# ------------------------------
# Logging setup
# ------------------------------
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def is_source_file(filename: str) -> bool:
    return filename.endswith(".c") or filename.endswith(".h")


def get_function_snippet(filepath: str, start_line: int, end_line: int) -> str:
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return "".join(lines[start_line - 1 : end_line])


def parse_and_store_entire_codebase(
    db_pool: SQLiteConnectionPool, parser, root_path: str
):
    root_path = os.path.abspath(root_path)
    logger.info(f"Starting full codebase parse at: {root_path}")
    for dirpath, _, filenames in os.walk(root_path):
        for fname in filenames:
            if is_source_file(fname):
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, root_path)
                logger.info(f"Parsing file: {rel_path}")
                info = extract_info_from_file(parser, full_path)
                store_file_info(db_pool, root_path, rel_path, info)
    logger.info("Finished parsing entire codebase.")


def store_file_info(
    db_pool: SQLiteConnectionPool, root_path: str, rel_path: str, info: dict
):
    logger.info(f"Storing symbols/functions for file: {rel_path}")
    conn = db_pool.acquire()
    try:
        file_id = insert_or_get_file_id(db_pool, rel_path)
        logger.debug(f"File ID for {rel_path}: {file_id}")

        # Insert structs
        for s in info.get("structs", []):
            logger.debug(f"Inserting struct: {s['name']}")
            insert_file_symbol(db_pool, file_id, "struct", s["name"], s["code"])

        # Insert typedefs
        for t in info.get("typedefs", []):
            logger.debug(f"Inserting typedef: {t['alias']}")
            insert_file_symbol(db_pool, file_id, "typedef", t["alias"], t["code"])

        # Insert globals
        for g in info.get("globals", []):
            logger.debug(f"Inserting global: {g['name']}")
            insert_file_symbol(db_pool, file_id, "global", g["name"], g["type"])

        # Insert functions
        abs_file_path = os.path.join(root_path, rel_path)
        all_funcs = info.get("functions", [])
        for func in all_funcs:
            snippet = get_function_snippet(
                abs_file_path, func["start_line"], func["end_line"]
            )
            hash_ = compute_code_hash(snippet)
            logger.debug(
                f"Inserting function: {func['name']} ({func['start_line']}-{func['end_line']})"
            )
            insert_function(
                db_pool,
                file_id,
                func["name"] or "",
                func.get("return_type") or "",
                json.dumps(func.get("parameters", [])),
                func["start_line"],
                func["end_line"],
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
    finally:
        db_pool.release(conn)


def _find_function_id(
    db_pool: SQLiteConnectionPool, file_id: int | None, func_name: str
):
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
