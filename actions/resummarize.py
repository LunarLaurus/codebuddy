# resummarize.py
import argparse
import subprocess
from pathlib import Path
import asyncio

from util.parser_setup import load_language, get_parser, parse_file_async
from code_analysis.code_extractor import extract_info_from_file
from code_analysis.code_map_builder import store_file_info
from summarizer import summarize_file_in_db, summarize_function_in_db
from util.db_utils import get_connection, insert_or_get_file_id


# ------------------------------
# Git helpers
# ------------------------------
def get_changed_files(rev1: str = "HEAD~1", rev2: str = "HEAD") -> list[str]:
    """Return list of changed file paths between two git revisions."""
    cmd = ["git", "diff", "--name-only", rev1, rev2]
    output = subprocess.check_output(cmd, text=True)
    return [line.strip() for line in output.splitlines() if line.strip()]


# ------------------------------
# Async resummarization
# ------------------------------
async def resummarize_file(conn, parser, full_path: Path, commit_sha: str):
    """Parse and summarize a single file."""
    if not full_path.is_file():
        return  # Skip deleted/renamed

    # Parse file asynchronously
    tree, code = await parse_file_async(parser, str(full_path))
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
    funcs = cur.fetchall()

    for fid, snippet in funcs:
        snippet = snippet or ""
        # Check if summary exists for this commit
        cur.execute(
            "SELECT 1 FROM function_summaries WHERE function_id=? AND commit_sha=?",
            (fid, commit_sha),
        )
        if cur.fetchone():
            continue
        summarize_function_in_db(conn, fid, snippet, commit_sha=commit_sha)


# ------------------------------
# Main
# ------------------------------
async def main_async(args):
    conn = get_connection(args.db)
    language = load_language("c")  # adjust if multi-language
    parser = get_parser(language)

    changed_files = get_changed_files(args.old_rev, args.new_rev)
    print(f"[INFO] Changed files: {changed_files}")

    # Process files concurrently
    tasks = [
        resummarize_file(conn, parser, Path(args.repo_path) / cf, args.new_rev)
        for cf in changed_files
    ]
    await asyncio.gather(*tasks)
    print("[INFO] Resummarization complete.")


def main():
    parser = argparse.ArgumentParser(
        description="Resummarize changed files/functions after a commit."
    )
    parser.add_argument("--db", default="summaries.db", help="Path to SQLite DB.")
    parser.add_argument("--old-rev", default="HEAD~1", help="Older commit")
    parser.add_argument("--new-rev", default="HEAD", help="Newer commit")
    parser.add_argument("--repo-path", default=".", help="Git repo root path")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
