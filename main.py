# main.py - Frontend menu (Project-based DB + DB listing)
import sys
import subprocess
import importlib
import logging
from pathlib import Path

from util.llm_client import DEFAULT_PATH
from util.project_manager import select_project, clone_project, list_projects, Project
from actions.run_analysis import run_full_analysis, build_code_map_from_db
from actions.resummarize import resummarize_changed_files
from actions.summarizer import print_pretty_overview

from rich.console import Console
from rich.table import Table

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
console = Console()

REQUIREMENTS_FILE = Path(__file__).parent / "requirements.txt"


def install_requirements():
    """Install packages from requirements.txt if missing."""
    if not REQUIREMENTS_FILE.exists():
        logger.warning("requirements.txt not found. Skipping auto-install.")
        return

    with open(REQUIREMENTS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            module_name = line.split("==")[0].replace("-", "_")
            try:
                importlib.import_module(module_name)
            except ModuleNotFoundError:
                logger.warning(
                    f"Module '{module_name}' not found. Installing {line}..."
                )
                subprocess.check_call([sys.executable, "-m", "pip", "install", line])
                logger.info(f"Module '{module_name}' installed.")


def print_menu(active_project: Project | None):
    table = Table(title="AI-Driven C Code Summarizer")
    table.add_column("Option", justify="center")
    table.add_column("Description", justify="left")
    active_info = f" [Active: {active_project.name}]" if active_project else ""
    table.add_row(
        "1", f"Parse & analyze entire codebase (file + function summaries){active_info}"
    )
    table.add_row(
        "2", f"Resummarize changed files/functions (Git incremental){active_info}"
    )
    table.add_row("3", f"View summaries (JSON){active_info}")
    table.add_row("4", f"View summaries (Pretty CLI){active_info}")
    table.add_row("5", "List cloned projects")
    table.add_row("6", "Clone new Git project")
    table.add_row("7", "List available DB files")
    table.add_row("8", "Swap active project")
    table.add_row("0", "Exit")
    console.print(table)


def prompt_project(active_project: Project | None) -> Project | None:
    """Prompt user to select a project if no active project is set."""
    if active_project:
        return active_project
    return select_project()


def list_db_files():
    """List all .db files in database/ folder."""
    db_dir = Path("database")
    db_dir.mkdir(exist_ok=True)
    db_files = sorted(db_dir.glob("*.db"))
    if not db_files:
        console.print("[bold yellow]No DB files found.[/bold yellow]")
    else:
        table = Table(title="Available DB Files")
        table.add_column("DB File", justify="left")
        for f in db_files:
            table.add_row(str(f))
        console.print(table)


def main():
    install_requirements()
    active_project: Project | None = None

    while True:
        print_menu(active_project)
        choice = input("Select an option: ").strip()

        if choice == "0":
            console.print("[bold green]Exiting.[/bold green]")
            sys.exit(0)

        elif choice in {"1", "2", "3", "4"}:
            project = prompt_project(active_project)
            if not project:
                console.print("[bold red]No project selected.[/bold red]")
                continue

            if choice == "1":
                summarize_functions = (
                    input("Summarize functions? (y/N): ").strip().lower() == "y"
                )
                run_full_analysis(project.path, project.db_path, summarize_functions)

            elif choice == "2":
                resummarize_changed_files(project.path, project.db_path)

            elif choice == "3":
                code_map = build_code_map_from_db(project.db_path)
                import json

                print(json.dumps(code_map, indent=2))

            elif choice == "4":
                code_map = build_code_map_from_db(project.db_path)
                print_pretty_overview(code_map, project.path)

        elif choice == "5":
            projects = list_projects()
            if not projects:
                console.print("[bold yellow]No cloned projects found.[/bold yellow]")
            else:
                table = Table(title="Cloned Projects")
                table.add_column("ID", justify="center")
                table.add_column("Name")
                table.add_column("Path")
                table.add_column("Git URL")
                table.add_column("DB Path")
                for proj in projects:
                    table.add_row(
                        str(proj.id),
                        proj.name,
                        proj.path,
                        proj.git_url or "-",
                        proj.db_path,
                    )
                console.print(table)

        elif choice == "6":
            git_url = input("Enter Git URL to clone: ").strip()
            name = input("Optional: project name: ").strip() or None
            try:
                project = clone_project(git_url, name)
                active_project = project  # Automatically set cloned project as active
                console.print(
                    f"[bold green]Project cloned:[/bold green] {project.path} (DB: {project.db_path})"
                )
            except Exception as e:
                console.print(f"[bold red]Failed to clone project:[/bold red] {e}")

        elif choice == "7":
            list_db_files()

        elif choice == "8":
            project = select_project()
            if project:
                active_project = project
                console.print(
                    f"[bold green]Active project set to:[/bold green] {project.name}"
                )

        else:
            console.print("[bold red]Invalid choice![/bold red]")


if __name__ == "__main__":
    main()
