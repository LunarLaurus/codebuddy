# summarizer.py

import os
from llm_client import generate_llm_summary
from db_utils import (
    insert_file_summary,
    insert_function_summary
)
from rich.console import Console
from rich.tree import Tree as RichTree
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box

def create_file_prompt(abs_file_path, info):
    """
    'abs_file_path' is the absolute path to read file content.
    """
    structs = info.get('structs', [])
    typedefs = info.get('typedefs', [])
    globals_ = info.get('globals', [])

    def indent_multiline(text):
        lines = text.split("\n")
        return "\n".join("    " + l for l in lines)

    struct_lines = []
    for s in structs:
        struct_lines.append(f"- {s['name']}:\n{indent_multiline(s['code'])}")

    typedef_lines = []
    for td in typedefs:
        typedef_lines.append(f"- {td['alias']}:\n{indent_multiline(td['code'])}")

    global_lines = []
    for g in globals_:
        global_lines.append(f"- {g['type']} {g['name']}")

    structs_info = "\n".join(struct_lines) if struct_lines else "None"
    typedefs_info = "\n".join(typedef_lines) if typedef_lines else "None"
    globals_info = "\n".join(global_lines) if globals_ else "None"

    if abs_file_path.endswith(".h"):
        file_type = "header file"
    else:
        file_type = "source file"

    with open(abs_file_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    prompt = f"""This is a C {file_type} at {abs_file_path}, with extracted data structures, typedefs, and globals.
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
    return prompt

def create_refine_prompt(abs_file_path, first_summary):
    with open(abs_file_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    prompt = f"""Below is the initial summary for file {abs_file_path}:

=== INITIAL SUMMARY ===
{first_summary}
=== END INITIAL SUMMARY ===

Refine or improve the summary for correctness and brevity.

=== CODE START ===
{content}
=== CODE END ===
"""
    return prompt

def summarize_file_in_db(conn, file_id, root_path, rel_path, info, commit_sha="HEAD"):
    abs_file_path = os.path.join(root_path, rel_path)

    prompt = create_file_prompt(abs_file_path, info)
    summary1 = generate_llm_summary(prompt)

    refine_prompt = create_refine_prompt(abs_file_path, summary1)
    summary2 = generate_llm_summary(refine_prompt)

    insert_file_summary(conn, file_id, commit_sha, summary1, summary2)

def summarize_function_in_db(conn, function_id, code_snippet, commit_sha="HEAD"):
    prompt1 = f"""Below is a single C function:

=== FUNCTION CODE ===
{code_snippet}
=== CODE END ===

Summarize its purpose, inputs, outputs, and key logic. Keep it concise.
"""
    summ1 = generate_llm_summary(prompt1)

    prompt2 = f"""Initial summary of the function:
{summ1}

Refine or improve it, focusing on correctness and brevity.
"""
    summ2 = generate_llm_summary(prompt2)

    insert_function_summary(conn, function_id, commit_sha, summ1, summ2)

def print_pretty_overview(code_map, root_path):
    console = Console()
    console.rule("[bold magenta]Codebase Overview[/bold magenta]")
    console.print("")

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
        panel_title = f"{fpath}"
        file_panel = Panel.fit(Text(panel_title, style="bold yellow"), border_style="yellow",
                               title="File", title_align="left")
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
        ftable = Table(box=box.SIMPLE, title="Functions", show_header=True, header_style="bold magenta")
        ftable.add_column("#", justify="right")
        ftable.add_column("Name", style="bold")
        ftable.add_column("Type", style="cyan")
        ftable.add_column("Lines", justify="right")

        i = 1
        for func in funcs:
            name = func["name"]
            rtype = func.get("return_type", "")
            line_str = f"{func['start_line']}–{func['end_line']}"
            ftable.add_row(str(i), name, rtype, line_str)
            i += 1

        console.print(ftable)
        console.print("")

        # For each function, show summary if present, and references
        for func in funcs:
            func_name = func["name"]
            func_summary = func.get("func_summary")
            callers = func.get("callers")
            callees = func.get("callees")

            # If there's any detail to show (summary or references), print a heading
            if func_summary or callers or callees:
                console.print(f"[bold cyan]Function '{func_name}' details:[/bold cyan]")

            # Optional summary
            if func_summary:
                console.print(Panel(func_summary, border_style="cyan"))

            # References
            if callers:
                console.print(f"[bold blue]Called by:[/bold blue] {', '.join(callers)}")
            if callees:
                console.print(f"[bold blue]Calls:[/bold blue] {', '.join(callees)}")

            if func_summary or callers or callees:
                console.print("")

    console.rule("[bold magenta]End of Overview[/bold magenta]")
