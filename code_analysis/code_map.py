# code_analysis/code_map.py
import json
from typing import Dict, Any
from util.db_utils import fetch_function_name_and_file, SQLiteConnectionPool


def build_code_map_from_db(
    db_pool: SQLiteConnectionPool, commit_sha="HEAD"
) -> Dict[str, Any]:
    """
    Build a unified code map from the database.
    Fully thread-safe with SQLiteConnectionPool.
    """
    conn = db_pool.acquire()
    try:
        code_map = {}
        cur = conn.cursor()

        # Files
        cur.execute("SELECT file_id, path FROM files")
        file_rows = cur.fetchall()
        file_id_to_path = {fid: path for fid, path in file_rows}
        for path in file_id_to_path.values():
            code_map[path] = {
                "functions": [],
                "structs": [],
                "typedefs": [],
                "globals": [],
            }

        # File summaries
        cur.execute(
            "SELECT file_id, summary, summary_refined FROM file_summaries WHERE commit_sha=?",
            (commit_sha,),
        )
        for fid, summ, ref in cur.fetchall():
            path = file_id_to_path.get(fid)
            if path:
                code_map[path]["file_summary"] = ref or summ

        # Symbols
        cur.execute(
            "SELECT file_id, symbol_type, symbol_name, code_snippet FROM file_symbols"
        )
        for fid, stype, name, snippet in cur.fetchall():
            path = file_id_to_path.get(fid)
            if not path:
                continue
            if stype == "struct":
                code_map[path]["structs"].append(
                    {"name": name, "code": snippet, "fields": []}
                )
            elif stype == "typedef":
                code_map[path]["typedefs"].append(
                    {"alias": name, "original": "<unknown>", "code": snippet}
                )
            elif stype == "global":
                code_map[path]["globals"].append({"name": name, "type": snippet})

        # Functions
        cur.execute(
            """
            SELECT function_id, file_id, name, return_type, parameters, start_line, end_line, is_prototype
            FROM functions
            """
        )
        func_map = {}
        for f_id, file_id, fname, rt, params_json, st, en, proto in cur.fetchall():
            path = file_id_to_path.get(file_id)
            if not path:
                continue
            try:
                params = json.loads(params_json)
            except:
                params = []
            func_map[f_id] = {
                "file_id": file_id,
                "path": path,
                "name": fname,
                "return_type": rt,
                "parameters": params,
                "start_line": st,
                "end_line": en,
                "prototype": bool(proto),
                "callers": [],
                "callees": [],
            }

        # Function summaries
        cur.execute(
            "SELECT function_id, summary, summary_refined FROM function_summaries WHERE commit_sha=?",
            (commit_sha,),
        )
        for fid, summ, ref in cur.fetchall():
            if fid in func_map:
                func_map[fid]["func_summary"] = ref or summ

        # References
        cur.execute("SELECT caller_id, callee_id FROM function_calls")
        for cid, calid in cur.fetchall():
            if cid in func_map and calid in func_map:
                c_name, _ = fetch_function_name_and_file(db_pool, cid)
                cal_name, _ = fetch_function_name_and_file(db_pool, calid)
                func_map[cid]["callees"].append(c_name)
                func_map[calid]["callers"].append(c_name)

        # Cull duplicates
        for fobj in func_map.values():
            fobj["callers"] = list(dict.fromkeys(fobj["callers"]))
            fobj["callees"] = list(dict.fromkeys(fobj["callees"]))

        # Unify duplicate functions
        unify_map = {}
        for fobj in func_map.values():
            key = (fobj["path"], fobj["name"], fobj["start_line"], fobj["end_line"])
            if key not in unify_map:
                unify_map[key] = fobj.copy()
            else:
                existing = unify_map[key]
                existing["callers"].extend(fobj["callers"])
                existing["callees"].extend(fobj["callees"])
                existing["callers"] = list(dict.fromkeys(existing["callers"]))
                existing["callees"] = list(dict.fromkeys(existing["callees"]))
                if not existing.get("func_summary") and fobj.get("func_summary"):
                    existing["func_summary"] = fobj["func_summary"]

        # Attach functions to code_map
        for fdata in unify_map.values():
            path = fdata["path"]
            func_entry = {
                k: fdata[k]
                for k in [
                    "name",
                    "return_type",
                    "parameters",
                    "start_line",
                    "end_line",
                    "prototype",
                ]
            }
            if fdata["callers"]:
                func_entry["callers"] = fdata["callers"]
            if fdata["callees"]:
                func_entry["callees"] = fdata["callees"]
            if fdata.get("func_summary"):
                func_entry["func_summary"] = fdata["func_summary"]
            code_map[path]["functions"].append(func_entry)

        return code_map
    finally:
        db_pool.release(conn)
