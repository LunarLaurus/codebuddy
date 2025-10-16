import os
import logging
from util.llm_client import generate_llm_summary
from util.db_utils import insert_file_summary, insert_function_summary
from rich.console import Console
from rich.tree import Tree as RichTree
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box

# ---------------- Logging ----------------
LOG = logging.getLogger("laurus-llm.summarizer")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


# ---------------- Prompt Creation ----------------
def create_file_prompt(abs_file_path, info):
    structs = info.get("structs", [])
    typedefs = info.get("typedefs", [])
    globals_ = info.get("globals", [])

    def indent_multiline(text):
        lines = text.split("\n")
        return "\n".join("    " + l for l in lines)

    struct_lines = [f"- {s['name']}:\n{indent_multiline(s['code'])}" for s in structs]
    typedef_lines = [
        f"- {td['alias']}:\n{indent_multiline(td['code'])}" for td in typedefs
    ]
    global_lines = [f"- {g['type']} {g['name']}" for g in globals_]

    structs_info = "\n".join(struct_lines) if struct_lines else "None"
    typedefs_info = "\n".join(typedef_lines) if typedef_lines else "None"
    globals_info = "\n".join(global_lines) if globals_ else "None"

    file_type = "header file" if abs_file_path.endswith(".h") else "source file"

    try:
        with open(abs_file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        LOG.exception("Failed to read file %s", abs_file_path)
        content = ""

    LOG.info(
        "Created prompt for file %s (length=%d chars)", abs_file_path, len(content)
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
    except Exception as e:
        LOG.exception("Failed to read file %s for refinement", abs_file_path)
        content = ""

    LOG.info(
        "Created refinement prompt for file %s (summary length=%d)",
        abs_file_path,
        len(first_summary),
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
def summarize_file_in_db(conn, file_id, root_path, rel_path, info, commit_sha="HEAD"):
    abs_file_path = os.path.join(root_path, rel_path)
    LOG.info("Summarizing file: %s", abs_file_path)

    try:
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

        insert_file_summary(conn, file_id, commit_sha, summary1, summary2)
        LOG.info("Summaries inserted into DB for file %s", abs_file_path)
    except Exception as e:
        LOG.exception("Failed to summarize file %s", abs_file_path)
        raise


def summarize_function_in_db(conn, function_id, code_snippet, commit_sha="HEAD"):
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

        insert_function_summary(conn, function_id, commit_sha, summ1, summ2)
        LOG.info("Function summaries inserted into DB for function ID %s", function_id)
    except Exception as e:
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
        file_panel = Panel.fit(
            Text(panel_title, style="bold yellow"),
            border_style="yellow",
            title="File",
            title_align="left",
        )
        console.print(file_panel)
        console.print("")

        # File summary
        file_summary = fdata.get("file_summary")
        if file_summary:
            console.print("[bold magenta]File Summary:[/bold magenta]")
            console.print(Panel(file_summary, border_style="magenta"))
            console.print("")

        funcs = fdata.get("functions", [])
        if not funcs:
            console.print("[dim]No functions found in this file.[/dim]")
            console.print("")
            continue

        # Basic table for function definitions
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
            name = func["name"]
            rtype = func.get("return_type", "")
            line_str = f"{func['start_line']}–{func['end_line']}"
            ftable.add_row(str(i), name, rtype, line_str)

        console.print(ftable)
        console.print("")

        # Function details
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
