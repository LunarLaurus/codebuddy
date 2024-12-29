# code_map_builder.py

import os
import json
from code_extractor import extract_info_from_file
from db_utils import (
    insert_or_get_file_id,
    insert_file_symbol,
    insert_function,
    insert_function_call,
    compute_code_hash
)

def is_source_file(filename):
    return filename.endswith('.c') or filename.endswith('.h')

def get_function_snippet(filepath, start_line, end_line):
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    snippet = lines[start_line - 1 : end_line]
    return "".join(snippet)

def parse_and_store_entire_codebase(conn, parser, root_path):
    root_path = os.path.abspath(root_path)
    for dirpath, _, filenames in os.walk(root_path):
        for fname in filenames:
            if is_source_file(fname):
                full_path = os.path.join(dirpath, fname)
                rel_path = os.path.relpath(full_path, root_path)
                info = extract_info_from_file(parser, full_path)
                store_file_info(conn, root_path, rel_path, info)

def store_file_info(conn, root_path, rel_path, info):
    file_id = insert_or_get_file_id(conn, rel_path)

    # Insert structs, typedefs, globals
    for s in info.get('structs', []):
        insert_file_symbol(conn, file_id, "struct", s['name'], s['code'])
    for t in info.get('typedefs', []):
        insert_file_symbol(conn, file_id, "typedef", t['alias'], t['code'])
    for g in info.get('globals', []):
        insert_file_symbol(conn, file_id, "global", g['name'], g['type'])

    # Insert functions
    all_funcs = info.get('functions', [])
    abs_file_path = os.path.join(root_path, rel_path)
    for func in all_funcs:
        snippet = get_function_snippet(abs_file_path, func['start_line'], func['end_line'])
        hash_ = compute_code_hash(snippet)
        insert_function(
            conn=conn,
            file_id=file_id,
            name=func['name'] or "",
            return_type=func['return_type'] or "",
            parameters=json.dumps(func['parameters']),
            start_line=func['start_line'],
            end_line=func['end_line'],
            is_prototype=func.get('prototype', False),
            code_hash=hash_,
            code_snippet=snippet
        )

    # Insert function calls
    for (caller_line, callee_name) in info.get('calls', []):
        caller_func_id = None
        for func in all_funcs:
            if func['start_line'] <= caller_line <= func['end_line']:
                caller_func_id = _find_function_id(conn, file_id, func['name'])
                break
        if caller_func_id:
            callee_func_id = _find_function_id(conn, None, callee_name)  # search globally
            if callee_func_id:
                insert_function_call(conn, caller_func_id, callee_func_id)

def _find_function_id(conn, file_id, func_name):
    """
    If file_id is not None, we search in that file. If not found, we return None.
    If file_id is None, we do a global search:
      1) Prefer real definitions (is_prototype=0).
      2) If none, fallback to any row with that name.
    """
    cur = conn.cursor()
    if file_id is not None:
        # Strictly in this file
        cur.execute("""
            SELECT function_id
              FROM functions
             WHERE file_id=? AND name=?
             LIMIT 1
        """, (file_id, func_name))
        row = cur.fetchone()
        return row[0] if row else None
    else:
        # Global search. Prefer real definitions first
        cur.execute("""
            SELECT function_id
              FROM functions
             WHERE name=? AND is_prototype=0
             LIMIT 1
        """, (func_name,))
        row = cur.fetchone()
        if row:
            return row[0]
        # if no real definition found, fallback to any function row with that name
        cur.execute("""
            SELECT function_id
              FROM functions
             WHERE name=?
             LIMIT 1
        """, (func_name,))
        row = cur.fetchone()
        return row[0] if row else None
