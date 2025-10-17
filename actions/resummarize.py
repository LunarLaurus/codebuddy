# resummarize.py

import os
import logging
import asyncio
import subprocess
from pathlib import Path

from rich.console import Console
from rich.tree import Tree as RichTree
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box

from util.llm_client import generate_llm_summary, set_mode_c, set_mode_file
from util.db_utils import (
    SQLiteConnectionPool,
    insert_file_summary,
    insert_function_summary,
    insert_or_get_file_id,
    mark_processed,
)
from code_analysis.parser import load_language, get_parser, parse_file_async
from code_analysis.code_extractor import extract_info_from_file
from code_analysis.code_map_builder import store_file_info

# ---------------- Logging ----------------
LOG = logging.getLogger("laurus-llm.resummarizer")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


# ---------------- Prompt Creation ----------------
def create_file_prompt(abs_file_path, info):
    structs = info.get("structs", [])
    typedefs = info.get("typedefs", [])
    globals_ = info.get("globals", [])

    def indent_multiline(text):
        return "\n".join("    " + l for l in text.split("\n"))

    structs_info = (
        "\n".join(f"- {s['name']}:\n{indent_multiline(s['code'])}" for s in structs)
        or "None"
    )
    typedefs_info = (
        "\n".join(
            f"- {td['alias']}:\n{indent_multiline(td['code'])}" for td in typedefs
        )
        or "None"
    )
    globals_info = "\n".join(f"- {g['type']} {g['name']}" for g in globals_) or "None"

    file_type = "header file" if abs_file_path.endswith(".h") else "source file"

    try:
        with open(abs_file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        LOG.exception("Failed to read file %s", abs_file_path)
        content = ""

    LOG.info(
        "Created prompt for file %s (length=%d chars) content=%s",
        abs_file_path,
        len(content),
        content,
    )
    return f"""This is a C {file_type} at {abs_file_path}, with extracted data structures, typedefs, and globals.
Summarize the file’s purpose and usage. Keep it concise.

Structures:
{structs_info}

Typedefs:
{typedefs_info}

Globals:
{globals_info}

=== CODE START ===
{content}
=== CODE END ===
"""


def create_refine_prompt(abs_file_path, first_summary):
    try:
        with open(abs_file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        LOG.exception("Failed to read file %s for refinement", abs_file_path)
        content = ""

    LOG.info(
        "Created refinement prompt for file %s (summary length=%d) summary=%s",
        abs_file_path,
        len(first_summary),
        first_summary,
    )
    return f"""Below is the initial summary for file {abs_file_path}:

=== INITIAL SUMMARY ===
{first_summary}
=== END INITIAL SUMMARY ===

Refine or improve the summary for correctness and brevity.

=== CODE START ===
{content}
=== CODE END ===
"""


# ---------------- Summarization Functions ----------------
def summarize_file_in_db(
    db_pool, file_id, root_path, rel_path, info, commit_sha="HEAD"
):
    abs_file_path = os.path.join(root_path, rel_path)
    LOG.info("Summarizing file: %s", abs_file_path)

    try:
        set_mode_file
        prompt = create_file_prompt(abs_file_path, info)
        summary1 = generate_llm_summary(prompt)
        LOG.info(
            "Initial summary generated for %s (length=%d)", abs_file_path, len(summary1)
        )

        refine_prompt = create_refine_prompt(abs_file_path, summary1)
        summary2 = generate_llm_summary(refine_prompt)
        LOG.info(
            "Refined summary generated for %s (length=%d)", abs_file_path, len(summary2)
        )

        insert_file_summary(db_pool, file_id, commit_sha, summary1, summary2)
        mark_processed(db_pool, "file", file_id, commit_sha)
        LOG.info("File summaries and status updated in DB for %s", abs_file_path)
    except Exception:
        LOG.exception("Failed to summarize file %s", abs_file_path)
        raise


def summarize_function_in_db(db_pool, function_id, code_snippet, commit_sha="HEAD"):
    LOG.info(
        "Summarizing function ID: %s (code length=%d)", function_id, len(code_snippet)
    )
    try:
        prompt1 = f"""Below is a single C function:

=== FUNCTION CODE ===
{code_snippet}
=== CODE END ===

Summarize its purpose, inputs, outputs, and key logic. Keep it concise.
"""
        summ1 = generate_llm_summary(prompt1)
        LOG.info("Initial function summary generated (length=%d)", len(summ1))

        prompt2 = f"""Initial summary of the function:
{summ1}

Refine or improve it, focusing on correctness and brevity.
"""
        summ2 = generate_llm_summary(prompt2)
        LOG.info("Refined function summary generated (length=%d)", len(summ2))

        insert_function_summary(db_pool, function_id, commit_sha, summ1, summ2)
        mark_processed(db_pool, "function", function_id, commit_sha)
        LOG.info(
            "Function summaries and status updated in DB for function ID %s",
            function_id,
        )
    except Exception:
        LOG.exception("Failed to summarize function ID %s", function_id)
        raise


async def summarize_function_in_db_async(
    db_pool, function_id: int, code_snippet: str, commit_sha: str = "HEAD"
):
    """
    Async wrapper around the existing (blocking) summarization pipeline.
    Blocking operations (LLM calls and DB writes) are executed via asyncio.to_thread
    so the coroutine does not block the event loop.
    """
    set_mode_c
    LOG.info(
        "Summarizing function ID: %s (code length=%d)", function_id, len(code_snippet)
    )

    try:
        prompt1 = f"""Below is a single C function:

=== FUNCTION CODE ===
{code_snippet}
=== CODE END ===

Summarize its purpose, inputs, outputs, and key logic. Keep it concise.
"""
        # run blocking LLM call in a thread
        summ1 = await asyncio.to_thread(generate_llm_summary, prompt1)
        LOG.info("Initial function summary generated (length=%d)", len(summ1 or ""))

        prompt2 = f"""Initial summary of the function:
{summ1}

Refine or improve it, focusing on correctness and brevity.
"""
        # run second LLM call in a thread
        summ2 = await asyncio.to_thread(generate_llm_summary, prompt2)
        LOG.info("Refined function summary generated (length=%d)", len(summ2 or ""))

        # Insert DB write in a thread (blocking DB helper)
        await asyncio.to_thread(
            insert_function_summary, db_pool, function_id, commit_sha, summ1, summ2
        )

        # mark processed in DB (also run in thread)
        await asyncio.to_thread(
            mark_processed, db_pool, "function", function_id, commit_sha
        )

        LOG.info(
            "Function summaries and status updated in DB for function ID %s",
            function_id,
        )
    except Exception:
        LOG.exception("Failed to summarize function ID %s", function_id)
        raise


# ---------------- Pretty Overview ----------------
def print_pretty_overview(code_map, root_path):
    console = Console()
    console.rule("[bold magenta]Codebase Overview[/bold magenta]")
    console.print("")
    LOG.info("Printing codebase overview for %d files", len(code_map))

    def build_path_tree(paths):
        root = {}
        for p in paths:
            parts = p.strip("/").split("/")
            d = root
            for part in parts[:-1]:
                d = d.setdefault(part, {})
            d[parts[-1]] = None
        return root

    tree_dict = build_path_tree(code_map.keys())
    rich_tree = RichTree("Project Root", style="bold green")

    def add_subtree(d, parent):
        for k in sorted(d.keys()):
            branch = parent.add(k, style="green")
            if isinstance(d[k], dict):
                add_subtree(d[k], branch)

    add_subtree(tree_dict, rich_tree)
    console.print(rich_tree)
    console.print("")

    for fpath, fdata in code_map.items():
        LOG.info("Displaying file: %s", fpath)
        panel_title = f"{fpath}"
        console.print(
            Panel.fit(
                Text(panel_title, style="bold yellow"),
                border_style="yellow",
                title="File",
                title_align="left",
            )
        )
        console.print("")

        file_summary = fdata.get("file_summary")
        if file_summary:
            console.print("[bold magenta]File Summary:[/bold magenta]")
            console.print(Panel(file_summary, border_style="magenta"))
            console.print("")

        funcs = fdata.get("functions", [])
        if not funcs:
            console.print("[dim]No functions found in this file.[/dim]\n")
            continue

        ftable = Table(
            box=box.SIMPLE,
            title="Functions",
            show_header=True,
            header_style="bold magenta",
        )
        ftable.add_column("#", justify="right")
        ftable.add_column("Name", style="bold")
        ftable.add_column("Type", style="cyan")
        ftable.add_column("Lines", justify="right")

        for i, func in enumerate(funcs, 1):
            line_str = f"{func['start_line']}–{func['end_line']}"
            ftable.add_row(str(i), func["name"], func.get("return_type", ""), line_str)

        console.print(ftable)
        console.print("")

        for func in funcs:
            func_name = func["name"]
            func_summary = func.get("func_summary")
            callers = func.get("callers")
            callees = func.get("callees")

            if func_summary or callers or callees:
                console.print(f"[bold cyan]Function '{func_name}' details:[/bold cyan]")

            if func_summary:
                console.print(Panel(func_summary, border_style="cyan"))
            if callers:
                console.print(f"[bold blue]Called by:[/bold blue] {', '.join(callers)}")
            if callees:
                console.print(f"[bold blue]Calls:[/bold blue] {', '.join(callees)}")
            if func_summary or callers or callees:
                console.print("")

    console.rule("[bold magenta]End of Overview[/bold magenta]")


# ------------------------------
# Git helpers
# ------------------------------
def get_changed_files(
    repo_path: str, old_rev: str = "HEAD~1", new_rev: str = "HEAD"
) -> list[str]:
    """Return list of changed file paths between two git revisions."""
    output = subprocess.check_output(
        ["git", "-C", repo_path, "diff", "--name-only", old_rev, new_rev], text=True
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


# ------------------------------
# Async resummarization
# ------------------------------
async def _resummarize_file_async(db_pool, parser, full_path: Path, commit_sha: str):
    if not full_path.is_file():
        return

    await parse_file_async(parser, str(full_path))
    info = extract_info_from_file(parser, str(full_path))

    conn = db_pool.acquire()
    try:
        store_file_info(db_pool, str(full_path), info)
        file_id = insert_or_get_file_id(db_pool, str(full_path))
        summarize_file_in_db(
            db_pool, file_id, str(full_path), info, commit_sha=commit_sha
        )

        cur = conn.cursor()
        cur.execute(
            "SELECT function_id, code_snippet FROM functions WHERE file_id=?",
            (file_id,),
        )
        for fid, snippet in cur.fetchall():
            snippet = snippet or ""
            cur.execute(
                "SELECT 1 FROM function_summaries WHERE function_id=? AND commit_sha=?",
                (fid, commit_sha),
            )
            if cur.fetchone():
                continue
            summarize_function_in_db_async(db_pool, fid, snippet, commit_sha=commit_sha)
    finally:
        db_pool.release(conn)


async def _resummarize_changed_async(
    repo_path: str, db_path: str, old_rev: str = "HEAD~1", new_rev: str = "HEAD"
):
    db_pool = SQLiteConnectionPool(db_path, pool_size=5)
    language = load_language("c")
    parser = get_parser(language)
    repo_path = Path(repo_path).resolve()

    changed_files = get_changed_files(str(repo_path), old_rev, new_rev)
    if not changed_files:
        print("[INFO] No changed files detected.")
        return

    tasks = [
        asyncio.to_thread(
            _resummarize_file_async, db_pool, parser, repo_path / f, new_rev
        )
        for f in changed_files
    ]
    await asyncio.gather(*tasks)
    print("[INFO] Resummarization complete.")


# ------------------------------
# Synchronous wrapper
# ------------------------------
def resummarize_changed_files(
    repo_path: str, db_path: str, old_rev: str = "HEAD~1", new_rev: str = "HEAD"
):
    asyncio.run(_resummarize_changed_async(repo_path, db_path, old_rev, new_rev))
