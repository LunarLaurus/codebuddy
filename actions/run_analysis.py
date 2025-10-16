# actions/run_analysis.py
import asyncio
from pathlib import Path

from util.db_utils import get_connection
from code_analysis.parser import load_language, get_parser
from code_analysis.code_map_builder import parse_and_store_entire_codebase
from code_analysis.summarization import resummarize_file, summarize_function_in_db
from code_analysis.code_map import build_code_map_from_db as _build_code_map_from_db
from code_analysis.output import print_code_map


async def _run_full_analysis_async(
    repo_path: str, db_path: str, summarize_functions: bool
):
    conn = get_connection(db_path)
    language = load_language("c")
    parser = get_parser(language)
    root_path = Path(repo_path).resolve()

    # Parse & store entire codebase
    await asyncio.to_thread(
        parse_and_store_entire_codebase, conn, parser, str(root_path)
    )

    # File-level summaries
    cur = conn.cursor()
    cur.execute("SELECT file_id, path FROM files")
    tasks = [
        resummarize_file(conn, parser, root_path / rel_path, commit_sha="HEAD")
        for _, rel_path in cur.fetchall()
    ]
    if tasks:
        await asyncio.gather(*tasks)

    # Function-level summaries
    if summarize_functions:
        cur.execute("SELECT function_id, code_snippet FROM functions")
        for fid, snippet in cur.fetchall():
            if snippet.strip():
                await asyncio.to_thread(
                    summarize_function_in_db, conn, fid, snippet, commit_sha="HEAD"
                )

    return _build_code_map_from_db(conn)


def run_full_analysis(
    repo_path: str, db_path: str = "summaries.db", summarize_functions: bool = False
):
    """Synchronous wrapper for menu."""
    return asyncio.run(
        _run_full_analysis_async(repo_path, db_path, summarize_functions)
    )


def build_code_map_from_db(db_path_or_conn):
    """Flexible: accept either DB path or connection."""
    if isinstance(db_path_or_conn, str):
        conn = get_connection(db_path_or_conn)
    else:
        conn = db_path_or_conn
    return _build_code_map_from_db(conn)
