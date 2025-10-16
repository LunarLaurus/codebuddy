# actions/resummarize.py
import asyncio
from pathlib import Path
import subprocess

from util.db_utils import get_connection, insert_or_get_file_id
from util.parser_setup import load_language, get_parser, parse_file_async
from code_analysis.code_extractor import extract_info_from_file
from code_analysis.code_map_builder import store_file_info
from summarizer import summarize_file_in_db, summarize_function_in_db


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
async def _resummarize_file_async(conn, parser, full_path: Path, commit_sha: str):
    """Parse and summarize a single file."""
    if not full_path.is_file():
        return  # Skip deleted/renamed

    # Parse file asynchronously
    await parse_file_async(parser, str(full_path))  # We only need parse tree; optional
    info = extract_info_from_file(parser, str(full_path))
    store_file_info(conn, str(full_path), info)

    # File-level summary
    file_id = insert_or_get_file_id(conn, str(full_path))
    summarize_file_in_db(conn, file_id, str(full_path), info, commit_sha=commit_sha)

    # Function-level summaries
    cur = conn.cursor()
    cur.execute(
        "SELECT function_id, code_snippet FROM functions WHERE file_id=?", (file_id,)
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
        summarize_function_in_db(conn, fid, snippet, commit_sha=commit_sha)


async def _resummarize_changed_async(
    repo_path: str, db_path: str, old_rev: str = "HEAD~1", new_rev: str = "HEAD"
):
    conn = get_connection(db_path)
    language = load_language("c")
    parser = get_parser(language)
    repo_path = Path(repo_path).resolve()

    changed_files = get_changed_files(str(repo_path), old_rev, new_rev)
    if not changed_files:
        print("[INFO] No changed files detected.")
        return

    tasks = [
        _resummarize_file_async(conn, parser, repo_path / f, commit_sha=new_rev)
        for f in changed_files
    ]
    await asyncio.gather(*tasks)
    print("[INFO] Resummarization complete.")


# ------------------------------
# Synchronous wrapper for menu
# ------------------------------
def resummarize_changed_files(
    repo_path: str,
    db_path: str = "summaries.db",
    old_rev: str = "HEAD~1",
    new_rev: str = "HEAD",
):
    """Callable from frontend menu."""
    asyncio.run(_resummarize_changed_async(repo_path, db_path, old_rev, new_rev))
