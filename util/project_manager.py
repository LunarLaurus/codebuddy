# util/project_manager.py

from pathlib import Path
import subprocess
from util.db_utils import SQLiteConnectionPool

PROJECTS_DIR = Path.home() / "projects"


def list_projects(db_pool: SQLiteConnectionPool):
    """Return list of projects stored in DB."""
    conn = db_pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute("SELECT project_id, name, path, git_url, last_commit FROM projects")
        return cur.fetchall()
    finally:
        db_pool.release(conn)


def clone_project(db_pool: SQLiteConnectionPool, git_url: str, name: str | None = None):
    """Clone a Git repo to projects directory and register it in DB."""
    PROJECTS_DIR.mkdir(exist_ok=True)
    if not name:
        name = git_url.split("/")[-1].replace(".git", "")
    project_path = PROJECTS_DIR / name
    if project_path.exists():
        raise FileExistsError(f"Directory {project_path} already exists.")

    subprocess.check_call(["git", "clone", git_url, str(project_path)])

    # Save project info in DB
    conn = db_pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO projects (name, path, git_url) VALUES (?, ?, ?)",
            (name, str(project_path), git_url),
        )
        conn.commit()
    finally:
        db_pool.release(conn)

    return project_path


def select_project(db_pool: SQLiteConnectionPool):
    """List projects and allow selection."""
    projects = list_projects(db_pool)
    if not projects:
        raise RuntimeError("No projects available. Please clone one first.")
    for i, (_, name, path, *_) in enumerate(projects, 1):
        print(f"[{i}] {name} - {path}")
    choice = int(input("Select project: "))
    return projects[choice - 1][2]  # Return path
