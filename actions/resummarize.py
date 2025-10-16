# actions/resummarize.py
import asyncio
from pathlib import Path
import subprocess

from util.db_utils import (
    SQLiteConnectionPool,
    insert_or_get_file_id,
)
from code_analysis.parser import load_language, get_parser, parse_file_async
from code_analysis.code_extractor import extract_info_from_file
from code_analysis.code_map_builder import store_file_info
from actions.summarizer import summarize_file_in_db, summarize_function_in_db


# ------------------------------
# Git helpers
# ------------------------------
def get_changed_files(
    repo_path: str, old_rev: str = "HEAD~1", new_rev: str = "HEAD"
) -> list[str]:
    """Return list of changed file paths between two git revisions."""
    cmd = ["git", "-C", repo_path, "diff", "--name-only", old_rev, new_rev]
    output = subprocess.check_output(cmd, text=True)
    return [line.strip() for line in output.splitlines() if line.strip()]


# ------------------------------
# Async resummarization
# ------------------------------
async def _resummarize_file_async(db_pool, parser, full_path: Path, commit_sha: str):
    """Parse and summarize a single file safely with a connection pool."""
    if not full_path.is_file():
        return  # Skip deleted/renamed

    # Parse file asynchronously
    await parse_file_async(parser, str(full_path))  # optional, just for tree

    # Extract code info
    info = extract_info_from_file(parser, str(full_path))

    # Acquire a connection from the pool for DB operations
    conn = db_pool.acquire()
    try:
        # Store symbols and functions
        store_file_info(conn, str(full_path), info)

        # File-level summary
        file_id = insert_or_get_file_id(db_pool, str(full_path))
        summarize_file_in_db(
            db_pool, file_id, str(full_path), info, commit_sha=commit_sha
        )

        # Function-level summaries
        cur = conn.cursor()
        cur.execute(
            "SELECT function_id, code_snippet FROM functions WHERE file_id=?",
            (file_id,),
        )
        for fid, snippet in cur.fetchall():
            snippet = snippet or ""
            # Skip if summary already exists for this commit
            cur.execute(
                "SELECT 1 FROM function_summaries WHERE function_id=? AND commit_sha=?",
                (fid, commit_sha),
            )
            if cur.fetchone():
                continue
            summarize_function_in_db(db_pool, fid, snippet, commit_sha=commit_sha)
    finally:
        db_pool.release(conn)


async def _resummarize_changed_async(
    repo_path: str, db_path: str, old_rev: str = "HEAD~1", new_rev: str = "HEAD"
):
    """Resummarize all changed files between two git revisions."""
    db_pool = SQLiteConnectionPool(db_path, pool_size=5)

    language = load_language("c")
    parser = get_parser(language)
    repo_path = Path(repo_path).resolve()

    changed_files = get_changed_files(str(repo_path), old_rev, new_rev)
    if not changed_files:
        print("[INFO] No changed files detected.")
        return

    # Run each file in a separate thread safely
    tasks = [
        asyncio.to_thread(
            _resummarize_file_async, db_pool, parser, repo_path / f, new_rev
        )
        for f in changed_files
    ]
    await asyncio.gather(*tasks)
    print("[INFO] Resummarization complete.")


# ------------------------------
# Synchronous wrapper for menu
# ------------------------------
def resummarize_changed_files(
    repo_path: str,
    db_path: str,
    old_rev: str = "HEAD~1",
    new_rev: str = "HEAD",
):
    """Callable from frontend menu."""
    asyncio.run(_resummarize_changed_async(repo_path, db_path, old_rev, new_rev))
