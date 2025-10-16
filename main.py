# main.py - Frontend menu

import sys
import os
from rich.console import Console
from rich.table import Table
from actions.run_analysis import run_full_analysis, build_code_map_from_db
from actions.resummarize import resummarize_changed_files

console = Console()


def print_menu():
    table = Table(title="AI-Driven C Code Summarizer")
    table.add_column("Option", justify="center")
    table.add_column("Description", justify="left")
    table.add_row("1", "Parse & analyze entire codebase (file + function summaries)")
    table.add_row("2", "Resummarize changed files/functions (Git incremental)")
    table.add_row("3", "View existing summaries in DB (JSON)")
    table.add_row("4", "View existing summaries in DB (Pretty CLI)")
    table.add_row("0", "Exit")
    console.print(table)


def main():
    while True:
        print_menu()
        choice = input("Select an option: ").strip()
        if choice == "0":
            console.print("[bold green]Exiting.[/bold green]")
            sys.exit(0)
        elif choice == "1":
            repo_path = input("Enter codebase path: ").strip()
            db_path = input("Enter DB path [summaries.db]: ").strip() or "summaries.db"
            summarize_functions = (
                input("Summarize functions? (y/N): ").strip().lower() == "y"
            )
            run_full_analysis(repo_path, db_path, summarize_functions)
        elif choice == "2":
            repo_path = input("Enter Git repo path: ").strip()
            db_path = input("Enter DB path [summaries.db]: ").strip() or "summaries.db"
            resummarize_changed_files(repo_path, db_path)
        elif choice == "3":
            db_path = input("Enter DB path [summaries.db]: ").strip() or "summaries.db"
            code_map = build_code_map_from_db(db_path)
            import json

            print(json.dumps(code_map, indent=2))
        elif choice == "4":
            db_path = input("Enter DB path [summaries.db]: ").strip() or "summaries.db"
            from summarizer import print_pretty_overview

            repo_path = input("Enter codebase path: ").strip()
            code_map = build_code_map_from_db(db_path)
            print_pretty_overview(code_map, repo_path)
        else:
            console.print("[bold red]Invalid choice![/bold red]")


if __name__ == "__main__":
    main()
