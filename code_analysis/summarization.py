# code_analysis/summarization.py
from code_analysis.code_extractor import extract_info_from_file
from code_analysis.code_map_builder import store_file_info
from actions.summarizer import summarize_file_in_db, summarize_function_in_db
from util.db_utils import insert_or_get_file_id
from pathlib import Path


async def resummarize_file(conn, parser, full_path: Path, commit_sha: str):
    if not full_path.is_file():
        return

    from code_analysis.parser import parse_file_async

    tree, code = await parse_file_async(parser, full_path)
    info = extract_info_from_file(parser, str(full_path))
    store_file_info(conn, str(full_path), info)

    file_id = insert_or_get_file_id(conn, str(full_path))
    summarize_file_in_db(conn, file_id, str(full_path), info, commit_sha=commit_sha)

    cur = conn.cursor()
    cur.execute(
        "SELECT function_id, code_snippet FROM functions WHERE file_id=?", (file_id,)
    )
    for fid, snippet in cur.fetchall():
        if snippet.strip():
            cur.execute(
                "SELECT 1 FROM function_summaries WHERE function_id=? AND commit_sha=?",
                (fid, commit_sha),
            )
            if cur.fetchone():
                continue
            summarize_function_in_db(conn, fid, snippet, commit_sha=commit_sha)
