# main.py
import sys
import argparse
import asyncio
from pathlib import Path

from util.db_utils import get_connection
from code_analysis.parser import load_language, get_parser
from code_analysis.code_map import build_code_map_from_db
from code_analysis.summarization import resummarize_file
from code_analysis.output import print_code_map
from code_analysis.parser import parse_file_async
from code_analysis.code_map_builder import parse_and_store_entire_codebase


async def main_async(args):
    conn = get_connection(args.db_path)
    language = load_language("c")
    parser = get_parser(language)

    if args.no_analysis:
        code_map = build_code_map_from_db(conn)
        print_code_map(
            code_map, json_out=args.json, pretty=args.pretty, root_path=args.path
        )
        return

    # Parse & store entire codebase
    parse_and_store_entire_codebase(conn, parser, args.path)

    # Summarize files
    if not args.no_llm:
        from code_analysis.code_extractor import extract_info_from_file

        cur = conn.cursor()
        cur.execute("SELECT file_id, path FROM files")
        root_abs = Path(args.path).resolve()
        tasks = []
        for fid, rel_path in cur.fetchall():
            abs_path = root_abs / rel_path
            tasks.append(resummarize_file(conn, parser, abs_path, commit_sha="HEAD"))
        await asyncio.gather(*tasks)

    # Optional function-level summarization
    if args.summarize_functions:
        cur = conn.cursor()
        cur.execute("SELECT function_id, code_snippet FROM functions")
        for fid, snippet in cur.fetchall():
            if snippet.strip():
                summarize_function_in_db(conn, fid, snippet, commit_sha="HEAD")

    code_map = build_code_map_from_db(conn)
    print_code_map(
        code_map, json_out=args.json, pretty=args.pretty, root_path=args.path
    )


def main():
    parser = argparse.ArgumentParser(description="DB-based code analysis")
    parser.add_argument("--path", required=True, help="Root path to the codebase.")
    parser.add_argument("--db-path", default="summaries.db", help="Path to SQLite DB.")
    parser.add_argument(
        "--no-llm", action="store_true", help="Skip file-level LLM summarization."
    )
    parser.add_argument(
        "--summarize-functions", action="store_true", help="Also summarize functions."
    )
    parser.add_argument(
        "--no-analysis", action="store_true", help="Skip parsing; just read DB."
    )
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--pretty", action="store_true", help="Output pretty overview.")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
