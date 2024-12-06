from llm_client import generate_llm_summary
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console
from rich.tree import Tree as RichTree
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich import box

def build_file_tree(paths, root_path):
    root_path = root_path.rstrip('/')
    root_len = len(root_path) + 1

    tree = {}
    for p in paths:
        rel_p = p[root_len:] if p.startswith(root_path) else p
        parts = rel_p.strip("/").split("/")
        d = tree
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = None
    return tree

def print_tree(d, parent):
    for name, val in sorted(d.items()):
        branch = parent.add(name, style="green")
        if isinstance(val, dict):
            print_tree(val, branch)

def indent_multiline(text):
    lines = text.split("\n")
    return "\n".join("    " + l for l in lines)

def create_file_prompt(filename, info):
    structs = info.get('structs', [])
    typedefs = info.get('typedefs', [])
    globals_ = info.get('globals', [])
    functions = info.get('functions', [])

    struct_lines = []
    for s in structs:
        struct_lines.append(f"- {s['name']}:\n{indent_multiline(s['code'])}")

    typedef_lines = []
    for td in typedefs:
        typedef_lines.append(f"- {td['alias']} = {td['original']}\n{indent_multiline(td['code'])}")

    global_lines = []
    for g in globals_:
        global_lines.append(f"- {g['type']} {g['name']}")

    structs_info = "\n".join(struct_lines) if struct_lines else "None"
    typedefs_info = "\n".join(typedef_lines) if typedef_lines else "None"
    globals_info = "\n".join(global_lines) if global_lines else "None"

    if filename.endswith('.h'):
        file_type_description = "header file (interfaces, data structures, and prototypes)"
    else:
        file_type_description = "source file (implementations)"

    with open(filename, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    prompt = f"""The following is a C {file_type_description}, along with extracted data structures, typedefs, globals, and functions/prototypes.
The code snippets for structs and typedefs are included below.
Summarize the file’s purpose and how these elements are intended to be used. Keep it concise.

File: {filename}

Key Data Structures:
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

def create_refine_prompt(filename, info, first_summary):
    structs = info.get('structs', [])
    typedefs = info.get('typedefs', [])
    globals_ = info.get('globals', [])
    functions = info.get('functions', [])

    struct_lines = []
    for s in structs:
        struct_lines.append(f"- {s['name']}:\n{indent_multiline(s['code'])}")

    typedef_lines = []
    for td in typedefs:
        typedef_lines.append(f"- {td['alias']} = {td['original']}\n{indent_multiline(td['code'])}")

    global_lines = []
    for g in globals_:
        global_lines.append(f"- {g['type']} {g['name']}")

    structs_info = "\n".join(struct_lines) if struct_lines else "None"
    typedefs_info = "\n".join(typedef_lines) if typedef_lines else "None"
    globals_info = "\n".join(global_lines) if global_lines else "None"

    if filename.endswith('.h'):
        file_type_description = "header file"
    else:
        file_type_description = "source file"

    with open(filename, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    prompt = f"""Below is the initial summary of the {file_type_description} named {filename}:

=== INITIAL SUMMARY ===
{first_summary}
=== END INITIAL SUMMARY ===

Now, you have the extracted data structures, typedefs, globals, functions/prototypes and the entire source file again.
Please produce a more accurate, improved, and concise summary of this file’s purpose and usage, leveraging the insights from the initial summary. Focus on clarity and correctness, and present this as the final summary of the file.

Key Data Structures:
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

def create_overview_text(code_map, root_path):
    lines = []
    lines.append("=== Codebase Overview ===")
    lines.append("")
    lines.append("Project File Tree:")

    file_paths = sorted(code_map.keys())
    tree = build_file_tree(file_paths, root_path)

    def print_tree_text(d, prefix=""):
        keys = sorted(d.keys())
        for i, k in enumerate(keys):
            connector = "└── " if i == len(keys)-1 else "├── "
            if d[k] is None:
                lines.append(prefix + connector + k)
            else:
                lines.append(prefix + connector + k)
                extension = "    " if i == len(keys)-1 else "│   "
                print_tree_text(d[k], prefix+extension)

    print_tree_text(tree)

    lines.append("")
    lines.append("Project Structure:")
    lines.append("")

    for fname, info in code_map.items():
        if not isinstance(info, dict):
            continue
        funcs = info.get('functions', [])
        lines.append(f"File: {fname}")
        if funcs:
            real_funcs = [f for f in funcs if not f.get('prototype', False)]
            protos = [f for f in funcs if f.get('prototype', False)]
            total_count = len(funcs)
            lines.append(f"  Functions/Prototypes ({total_count} total):")
            i = 1
            for func in real_funcs:
                name = func['name'] or "None"
                rtype = func['return_type'] or "None"
                params = func['parameters'] or []
                params_str = ", ".join(params)
                if params_str:
                    func_line = f"    {i}. {rtype} {name}({params_str}) [lines {func['start_line']}–{func['end_line']}]"
                else:
                    func_line = f"    {i}. {rtype} {name}() [lines {func['start_line']}–{func['end_line']}]"
                lines.append(func_line)
                i += 1
            for func in protos:
                name = func['name'] or "None"
                rtype = func['return_type'] or "None"
                params = func['parameters'] or []
                params_str = ", ".join(params)
                if params_str:
                    func_line = f"    {i}. (prototype) {rtype} {name}({params_str}) [lines {func['start_line']}–{func['end_line']}]"
                else:
                    func_line = f"    {i}. (prototype) {rtype} {name}() [lines {func['start_line']}–{func['end_line']}]"
                lines.append(func_line)
                i += 1
            lines.append(f"  Summary: {len(real_funcs)} definitions, {len(protos)} prototypes.")
        else:
            lines.append("  No functions or prototypes found.")

        s_count = len(info.get('structs', []))
        t_count = len(info.get('typedefs', []))
        g_count = len(info.get('globals', []))
        lines.append(f"  Structs: {s_count}, Typedefs: {t_count}, Globals: {g_count}")

        structs = info.get('structs', [])
        if structs:
            lines.append("  Struct Definitions:")
            for s in structs:
                lines.append(f"    {s['name']}:")
                for l in s['code'].split("\n"):
                    lines.append(f"      {l}")

        typedefs = info.get('typedefs', [])
        if typedefs:
            lines.append("  Typedef Definitions:")
            for td in typedefs:
                lines.append(f"    {td['alias']} = {td['original']}:")
                for l in td['code'].split("\n"):
                    lines.append(f"      {l}")

        final_summary = info.get('file_summary', None)
        if final_summary:
            lines.append("  File Summary:")
            lines.append(f"    {final_summary}")

        lines.append("")

    project_summary = code_map.get('_project_summary', None)
    if project_summary:
        lines.append("=== Final Project Summary ===")
        lines.append(project_summary)
        lines.append("")

    return "\n".join(lines)

def print_pretty_overview(code_map, root_path):
    console = Console()

    console.rule("[bold magenta]Codebase Overview[/bold magenta]")
    console.print("")

    console.print("[bold cyan]Project File Tree:[/bold cyan]")
    file_paths = sorted(code_map.keys())
    tree_dict = build_file_tree(file_paths, root_path)
    rich_tree = RichTree("Project Root", style="bold green")
    def add_tree_nodes(d, parent):
        for k in sorted(d.keys()):
            branch = parent.add(k, style="green")
            if isinstance(d[k], dict):
                add_tree_nodes(d[k], branch)

    add_tree_nodes(tree_dict, rich_tree)
    console.print(rich_tree)
    console.print("")

    console.print("[bold cyan]Project Structure:[/bold cyan]")
    console.print("")

    for fname, info in code_map.items():
        if not isinstance(info, dict):
            continue

        file_panel = Panel.fit(Text(fname, style="bold yellow"), border_style="yellow", title="File", title_align="left")
        console.print(file_panel)
        console.print("")

        funcs = info.get('functions', [])
        s_count = len(info.get('structs', []))
        t_count = len(info.get('typedefs', []))
        g_count = len(info.get('globals', []))

        ftable = Table(box=box.SIMPLE, title="Functions/Prototypes", show_header=True, header_style="bold magenta")
        ftable.add_column("#", justify="right")
        ftable.add_column("Name", style="bold")
        ftable.add_column("Type", style="cyan")
        ftable.add_column("Params", style="dim")
        ftable.add_column("Lines", justify="right")

        i = 1
        if funcs:
            real_funcs = [f for f in funcs if not f.get('prototype', False)]
            protos = [f for f in funcs if f.get('prototype', False)]
            for func in real_funcs:
                name = func['name'] or "None"
                rtype = func['return_type'] or "None"
                params = ", ".join(func['parameters'] or [])
                lines_str = f"{func['start_line']}–{func['end_line']}"
                ftable.add_row(str(i), name, rtype, params, lines_str)
                i += 1
            for func in protos:
                name = func['name'] or "None"
                rtype = func['return_type'] or "None"
                params = ", ".join(func['parameters'] or [])
                lines_str = f"{func['start_line']}–{func['end_line']}"
                ftable.add_row(str(i), f"(prototype) {name}", rtype, params, lines_str)
                i += 1

            console.print(ftable)
            console.print(f"[bold]{len(real_funcs)} definitions, {len(protos)} prototypes.[/bold]")
        else:
            console.print("[dim]No functions or prototypes found.[/dim]")

        console.print(f"Structs: {s_count}, Typedefs: {t_count}, Globals: {g_count}\n", style="cyan")

        # Struct definitions
        if info.get('structs', []):
            console.print("[bold blue]Struct Definitions:[/bold blue]")
            for s in info['structs']:
                console.print(Text(s['name'], style="bold"))
                console.print(Panel(indent_multiline(s['code']), title="Code", border_style="blue"))
            console.print("")

        # Typedef definitions
        if info.get('typedefs', []):
            console.print("[bold blue]Typedef Definitions:[/bold blue]")
            for td in info['typedefs']:
                console.print(Text(f"{td['alias']} = {td['original']}", style="bold"))
                console.print(Panel(indent_multiline(td['code']), title="Code", border_style="blue"))
            console.print("")

        final_summary = info.get('file_summary', None)
        if final_summary:
            console.print("[bold magenta]File Summary:[/bold magenta]")
            console.print(Panel(final_summary, border_style="magenta"))
            console.print("")

    project_summary = code_map.get('_project_summary', None)
    if project_summary:
        console.rule("[bold magenta]Final Project Summary[/bold magenta]")
        console.print(Panel(project_summary, border_style="magenta"))

def run_llm_task(prompt):
    return generate_llm_summary(prompt)

def summarize_files(code_map):
    tasks = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for fname, info in code_map.items():
            if fname.startswith('_'):
                continue
            if not isinstance(info, dict):
                continue
            prompt = create_file_prompt(fname, info)
            future = executor.submit(run_llm_task, prompt)
            tasks.append((fname, future))

        for fname, fut in tasks:
            summary = fut.result()
            code_map[fname]['file_summary'] = summary
    return [info['file_summary'] for fname, info in code_map.items() if isinstance(info, dict) and 'file_summary' in info]

def refine_file_summaries(code_map):
    tasks = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        for fname, info in code_map.items():
            if fname.startswith('_'):
                continue
            if not isinstance(info, dict):
                continue
            first_summary = info.get('file_summary', None)
            if not first_summary:
                continue
            prompt = create_refine_prompt(fname, info, first_summary)
            future = executor.submit(run_llm_task, prompt)
            tasks.append((fname, future))

        for fname, fut in tasks:
            refined_summary = fut.result()
            code_map[fname]['file_refined_summary'] = refined_summary

def summarize_project(file_summaries):
    prompt = (
        "Below are short summaries of several C source and header files from a project.\n"
        "Produce a very concise overall summary of the entire codebase, focusing on architecture, data flow, and interactions.\n\n"
        "File Summaries:\n"
    )
    for i, fs in enumerate(file_summaries, 1):
        prompt += f"{i}. {fs}\n"

    return generate_llm_summary(prompt)
