# util/project_manager.py

from pathlib import Path
import subprocess
from dataclasses import dataclass
from typing import Optional, List
from util.db_utils import SQLiteConnectionPool
from rich.console import Console
import re

console = Console()
PROJECTS_DIR = Path.home() / "projects"
PROJECTS_DIR.mkdir(exist_ok=True)

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    project_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    git_url TEXT,
    last_commit TEXT,
    db_path TEXT NOT NULL
);
"""

# Internal DB for project metadata
internal_database_pool = SQLiteConnectionPool("internal.db", pool_size=5, schema=SCHEMA)


@dataclass
class Project:
    id: int
    name: str
    path: str
    git_url: Optional[str]
    last_commit: Optional[str]
    db_path: str


def _sanitize_project_name(name: str) -> str:
    """Safe filename for DB: lowercase, underscores, collapse duplicates."""
    base = name.lower()
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^a-z0-9_-]+", "_", base)
    base = re.sub(r"_+", "_", base)
    return base.strip("_") or "project"


def list_projects() -> List[Project]:
    """Return list of projects stored in DB as Project objects."""
    conn = internal_database_pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT project_id, name, path, git_url, last_commit, db_path FROM projects"
        )
        rows = cur.fetchall()
        return [Project(*r) for r in rows]
    finally:
        internal_database_pool.release(conn)


def clone_project(git_url: str, name: Optional[str] = None) -> Project:
    """Clone a Git repo to projects directory and register it in DB."""
    if not name:
        name = git_url.rstrip("/").split("/")[-1].replace(".git", "")

    # Ensure database folder exists
    db_dir = Path("database")
    db_dir.mkdir(parents=True, exist_ok=True)

    # Compute DB path before cloning
    safe_name = _sanitize_project_name(name)
    db_path = str(db_dir / f"{safe_name}.db")

    # Now clone the repo
    project_path = PROJECTS_DIR / safe_name
    suffix = 1
    original_path = project_path
    while project_path.exists():
        project_path = PROJECTS_DIR / f"{original_path.name}_{suffix}"
        suffix += 1

    subprocess.check_call(["git", "clone", git_url, str(project_path)])

    # Save project info in DB
    conn = internal_database_pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO projects (name, path, git_url, last_commit, db_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (project_path.name, str(project_path), git_url, None, db_path),
        )
        project_id = cur.lastrowid
        conn.commit()
    finally:
        internal_database_pool.release(conn)

    return Project(
        id=project_id,
        name=project_path.name,
        path=str(project_path),
        git_url=git_url,
        last_commit=None,
        db_path=db_path,
    )


def select_project() -> Optional[Project]:
    """List projects and allow selection. Returns Project object or None."""
    projects = list_projects()
    if not projects:
        console.print(
            "[bold yellow]No projects available. Please clone one first.[/bold yellow]"
        )
        return None

    for i, proj in enumerate(projects, 1):
        console.print(f"[{i}] {proj.name} - {proj.path} (DB: {proj.db_path})")

    while True:
        choice = input("Select project: ").strip()
        if not choice.isdigit() or not (1 <= int(choice) <= len(projects)):
            console.print("[bold red]Invalid selection. Try again.[/bold red]")
        else:
            break

    return projects[int(choice) - 1]
