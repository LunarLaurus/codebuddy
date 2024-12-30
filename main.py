# main.py

import sys
import json
import argparse
import os
import sqlite3

from parser_setup import load_language, create_parser
from code_map_builder import parse_and_store_entire_codebase
from summarizer import (
    summarize_file_in_db,
    summarize_function_in_db,
    print_pretty_overview
)
from db_utils import (
    get_connection
)


def build_code_map_from_db(conn, commit_sha="HEAD"):
    """
    Collect data from the DB into a code_map, unify duplicate function entries
    in the same file (same name & lines), and cull duplicate references.
    """
    code_map = {}
    cur = conn.cursor()

    # 1) Gather files
    cur.execute("SELECT file_id, path FROM files")
    file_rows = cur.fetchall()
    file_id_to_path = {}
    for (fid, rel_path) in file_rows:
        file_id_to_path[fid] = rel_path
        code_map[rel_path] = {
            "functions": [],
            "structs": [],
            "typedefs": [],
            "globals": []
        }

    # 2) File Summaries
    cur.execute("""
      SELECT file_id, summary, summary_refined
        FROM file_summaries
       WHERE commit_sha=?
    """, (commit_sha,))
    for (fid, summ, ref) in cur.fetchall():
        relp = file_id_to_path.get(fid)
        if not relp:
            continue
        final_summ = ref if ref else summ
        if final_summ:
            code_map[relp]["file_summary"] = final_summ

    # 3) file_symbols (structs, typedefs, globals)
    cur.execute("SELECT file_id, symbol_type, symbol_name, code_snippet FROM file_symbols")
    for (fid, stype, sname, snippet) in cur.fetchall():
        relp = file_id_to_path.get(fid)
        if not relp:
            continue
        if stype == "struct":
            code_map[relp]["structs"].append({
                "name": sname,
                "code": snippet,
                "fields": []
            })
        elif stype == "typedef":
            code_map[relp]["typedefs"].append({
                "alias": sname,
                "original": "<unknown>",
                "code": snippet
            })
        elif stype == "global":
            code_map[relp]["globals"].append({
                "name": sname,
                "type": snippet
            })

    # 4) Collect all function rows
    import json as pyjson
    cur.execute("""
      SELECT function_id, file_id, name, return_type, parameters,
             start_line, end_line, is_prototype
        FROM functions
    """)
    func_map = {}
    for row in cur.fetchall():
        (f_id, file_id, fname, rt, params_json, st, en, proto) = row
        relp = file_id_to_path.get(file_id)
        if not relp:
            continue
        try:
            params = pyjson.loads(params_json)
        except:
            params = []
        func_map[f_id] = {
            "file_id": file_id,
            "path": relp,
            "name": fname,
            "return_type": rt,
            "parameters": params,
            "start_line": st,
            "end_line": en,
            "prototype": bool(proto),
            "callers": [],
            "callees": []
        }

    # 5) Function Summaries
    cur.execute("""
      SELECT function_id, summary, summary_refined
        FROM function_summaries
       WHERE commit_sha=?
    """, (commit_sha,))
    for (fid_, summ, ref) in cur.fetchall():
        if fid_ not in func_map:
            continue
        final_summ = ref if ref else summ
        if final_summ:
            func_map[fid_]["func_summary"] = final_summ

    # 6) References (function_calls)
    cur.execute("SELECT caller_id, callee_id FROM function_calls")
    from db_utils import fetch_function_name_and_file
    for (cid, calid) in cur.fetchall():
        if cid in func_map and calid in func_map:
            c_unique, _ = fetch_function_name_and_file(conn, cid)
            cal_unique, _ = fetch_function_name_and_file(conn, calid)
            func_map[cid]["callees"].append(c_unique)
            func_map[calid]["callers"].append(c_unique)

    # 7) Cull duplicates from references (callers/callees)
    for fobj in func_map.values():
        fobj["callers"] = list(dict.fromkeys(fobj["callers"]))
        fobj["callees"] = list(dict.fromkeys(fobj["callees"]))

    # 8) Now unify duplicates if we have multiple function_id rows for the
    #    same file + same name + same lines. We'll pick the "best" info from each.
    #    Key = (path, name, start_line, end_line).
    unify_map = {}
    for fid_, fobj in func_map.items():
        unify_key = (fobj["path"], fobj["name"], fobj["start_line"], fobj["end_line"])
        if unify_key not in unify_map:
            unify_map[unify_key] = {
                "name": fobj["name"],
                "return_type": fobj["return_type"],
                "parameters": fobj["parameters"],
                "start_line": fobj["start_line"],
                "end_line": fobj["end_line"],
                "prototype": fobj["prototype"],
                "callers": fobj["callers"][:],
                "callees": fobj["callees"][:],
                "func_summary": fobj.get("func_summary", None)
            }
        else:
            # Merge data: references, summary, etc.
            existing = unify_map[unify_key]
            # Keep or union references
            existing["callers"].extend(fobj["callers"])
            existing["callees"].extend(fobj["callees"])
            existing["callers"] = list(dict.fromkeys(existing["callers"]))
            existing["callees"] = list(dict.fromkeys(existing["callees"]))

            # If we have a summary in either, pick one
            if not existing.get("func_summary") and "func_summary" in fobj:
                existing["func_summary"] = fobj["func_summary"]

            # Return type or parameters might differ if the header is incomplete
            # We'll just keep the existing or update if the new one is "better"
            # but to keep it simple, we do nothing unless you want more logic
            # about picking "non-empty" return_type.
            # existing["return_type"] = ???

    # attach each final unified function to code_map
    for (path, name, st, en), fdata in unify_map.items():
        final_func = {
            "name": fdata["name"],
            "return_type": fdata["return_type"],
            "parameters": fdata["parameters"],
            "start_line": fdata["start_line"],
            "end_line": fdata["end_line"],
            "prototype": fdata["prototype"]
        }
        if fdata["callers"]:
            final_func["callers"] = fdata["callers"]
        if fdata["callees"]:
            final_func["callees"] = fdata["callees"]
        if fdata["func_summary"]:
            final_func["func_summary"] = fdata["func_summary"]

        code_map[path]["functions"].append(final_func)

    return code_map


def main():
    parser = argparse.ArgumentParser(description="DB-based code analysis (relative paths).")
    parser.add_argument("--path", required=True, help="Root path to the codebase.")
    parser.add_argument("--db-path", default="summaries.db", help="Path to SQLite DB.")
    parser.add_argument("--no-llm", action="store_true", help="Skip file-level LLM summarization.")
    parser.add_argument("--summarize-functions", action="store_true", help="Also do function-level summarization.")
    parser.add_argument("--no-analysis", action="store_true", help="Skip parsing/summarization; just read DB.")
    parser.add_argument("--json", action="store_true", help="Output JSON.")
    parser.add_argument("--pretty", action="store_true", help="Output Rich-based overview.")
    args = parser.parse_args()

    conn = get_connection(args.db_path)
    language = load_language()
    parser_obj = create_parser(language)

    if args.no_analysis:
        code_map = build_code_map_from_db(conn, commit_sha="HEAD")
        if args.json:
            print(json.dumps(code_map, indent=2))
        elif args.pretty:
            print_pretty_overview(code_map, args.path)
        else:
            print("[INFO] No output format used. Use --json or --pretty.")
        return

    # 1) Parse & store
    parse_and_store_entire_codebase(conn, parser_obj, args.path)

    # 2) Summarize files unless --no-llm
    if not args.no_llm:
        from code_extractor import extract_info_from_file
        cur = conn.cursor()
        cur.execute("SELECT file_id, path FROM files")
        rows = cur.fetchall()
        root_abs = os.path.abspath(args.path)
        for (fid, rel_path) in rows:
            abs_path = os.path.join(root_abs, rel_path)
            info = extract_info_from_file(parser_obj, abs_path)
            summarize_file_in_db(conn, fid, root_abs, rel_path, info, commit_sha="HEAD")

    # 3) Summarize functions if requested
    if args.summarize_functions:
        cur = conn.cursor()
        cur.execute("SELECT function_id, code_snippet FROM functions")
        for (fid_, snippet) in cur.fetchall():
            if snippet.strip():
                summarize_function_in_db(conn, fid_, snippet, commit_sha="HEAD")

    # 4) Final output
    code_map = build_code_map_from_db(conn, commit_sha="HEAD")
    if args.json:
        print(json.dumps(code_map, indent=2))
    elif args.pretty:
        print_pretty_overview(code_map, args.path)
    else:
        print("[INFO] Done. Use --json or --pretty for output.")


if __name__ == "__main__":
    sys.exit(main())
