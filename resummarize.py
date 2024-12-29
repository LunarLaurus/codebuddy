# resummarize.py

import subprocess
import argparse
import os
from parser_setup import load_language, create_parser
from code_extractor import extract_info_from_file
from code_map_builder import store_file_info, get_function_snippet
from summarizer import summarize_file_in_db, summarize_function_in_db
from db_utils import (
    get_connection,
    insert_or_get_file_id,
    compute_code_hash
)

def get_changed_files(rev1="HEAD~1", rev2="HEAD"):
    cmd = ["git", "diff", "--name-only", rev1, rev2]
    output = subprocess.check_output(cmd, text=True)
    changed = [line.strip() for line in output.splitlines() if line.strip()]
    return changed

def main():
    parser = argparse.ArgumentParser(description="Resummarize changed files (and functions) after a commit.")
    parser.add_argument("--db", default="summaries.db", help="Path to the SQLite database.")
    parser.add_argument("--old-rev", default="HEAD~1", help="Older commit/branch/tag")
    parser.add_argument("--new-rev", default="HEAD", help="Newer commit/branch/tag")
    parser.add_argument("--repo-path", default=".", help="Path to Git repo root.")
    args = parser.parse_args()

    conn = get_connection(args.db)
    language = load_language()
    ts_parser = create_parser(language)

    changed_files = get_changed_files(args.old_rev, args.new_rev)
    print("Changed files:", changed_files)

    for cf in changed_files:
        full_path = os.path.join(args.repo_path, cf)
        if not os.path.isfile(full_path):
            # Skip deletes or renamed
            continue

        # Re-parse this file, store new AST info
        info = extract_info_from_file(ts_parser, full_path)
        store_file_info(conn, full_path, info)

        # Summarize the file-level
        file_id = insert_or_get_file_id(conn, full_path)
        summarize_file_in_db(conn, file_id, full_path, info, commit_sha=args.new_rev)

        # Now do function-level summarization for changed functions
        # We'll do a naive approach: if function code_hash changed or there's no summary, re-summarize
        cur = conn.cursor()

        # We'll find all current functions for this file
        cur.execute("SELECT function_id, start_line, end_line, code_hash, code_snippet FROM functions WHERE file_id=?", (file_id,))
        current_funcs = cur.fetchall()

        for (fid, st_line, en_line, new_hash, snippet) in current_funcs:
            # check if there's an existing summary for (fid, new_rev)
            cur2 = conn.cursor()
            cur2.execute("SELECT summary FROM function_summaries WHERE function_id=? AND commit_sha=?", (fid, args.new_rev))
            row = cur2.fetchone()
            if row:
                # we already have a summary for this commit
                continue

            # If we wanted to check old hash, we'd have to store an old record. For now, let's always do a new summary
            summarize_function_in_db(conn, fid, snippet, commit_sha=args.new_rev)

    print("Resummarization complete.")

if __name__ == "__main__":
    main()
