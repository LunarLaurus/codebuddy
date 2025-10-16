# db_utils.py

import sqlite3
import os
import hashlib
import threading
from queue import Queue, Empty

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS file_symbols (
    symbol_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    symbol_type TEXT NOT NULL,
    symbol_name TEXT,
    code_snippet TEXT,
    FOREIGN KEY(file_id) REFERENCES files(file_id)
);

CREATE TABLE IF NOT EXISTS functions (
    function_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    return_type TEXT,
    parameters TEXT,
    start_line INTEGER,
    end_line INTEGER,
    is_prototype BOOLEAN,
    code_hash TEXT,
    code_snippet TEXT,
    FOREIGN KEY(file_id) REFERENCES files(file_id)
);

CREATE TABLE IF NOT EXISTS function_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_id INTEGER NOT NULL,
    callee_id INTEGER NOT NULL,
    FOREIGN KEY(caller_id) REFERENCES functions(function_id),
    FOREIGN KEY(callee_id) REFERENCES functions(function_id)
);

CREATE TABLE IF NOT EXISTS file_summaries (
    file_summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    commit_sha TEXT,
    summary TEXT,
    summary_refined TEXT,
    UNIQUE(file_id, commit_sha),
    FOREIGN KEY(file_id) REFERENCES files(file_id)
);

CREATE TABLE IF NOT EXISTS function_summaries (
    function_summary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    function_id INTEGER NOT NULL,
    commit_sha TEXT,
    summary TEXT,
    summary_refined TEXT,
    UNIQUE(function_id, commit_sha),
    FOREIGN KEY(function_id) REFERENCES functions(function_id)
);

CREATE TABLE IF NOT EXISTS commits (
    commit_sha TEXT PRIMARY KEY,
    timestamp DATETIME,
    author TEXT,
    message TEXT
);
"""


# ------------------------------
# Connection pool
# ------------------------------
class SQLiteConnectionPool:
    """
    Simple thread-safe SQLite connection pool.
    """

    def __init__(self, db_path="summaries.db", pool_size=5):
        self.db_path = db_path
        self.pool_size = pool_size
        self.pool = Queue(maxsize=pool_size)
        self.lock = threading.Lock()  # protects pool initialization
        self._init_pool()

    def _init_pool(self):
        with self.lock:
            must_init = not os.path.exists(self.db_path)
            for _ in range(self.pool_size):
                conn = sqlite3.connect(self.db_path, check_same_thread=False)
                if must_init:
                    conn.executescript(SCHEMA)
                    conn.commit()
                    must_init = False
                self.pool.put(conn)

    def acquire(self, timeout=None):
        return self.pool.get(timeout=timeout)

    def release(self, conn):
        self.pool.put(conn)

    def close_all(self):
        while not self.pool.empty():
            try:
                conn = self.pool.get_nowait()
                conn.close()
            except Empty:
                break


# ------------------------------
# Utility
# ------------------------------
def compute_code_hash(code_text: str) -> str:
    return hashlib.md5(code_text.encode("utf-8")).hexdigest()


# ------------------------------
# Database operations
# ------------------------------
def insert_or_get_file_id(pool: SQLiteConnectionPool, path: str) -> int:
    conn = pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO files (path) VALUES (?)", (path,))
        conn.commit()
        cur.execute("SELECT file_id FROM files WHERE path = ?", (path,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        pool.release(conn)


def insert_file_symbol(
    pool: SQLiteConnectionPool,
    file_id: int,
    symbol_type: str,
    symbol_name: str,
    code_snippet: str,
):
    conn = pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO file_symbols (file_id, symbol_type, symbol_name, code_snippet)
            VALUES (?, ?, ?, ?)
        """,
            (file_id, symbol_type, symbol_name, code_snippet),
        )
        conn.commit()
    finally:
        pool.release(conn)


def insert_function(
    pool: SQLiteConnectionPool,
    file_id: int,
    name: str,
    return_type: str,
    parameters: str,
    start_line: int,
    end_line: int,
    is_prototype: bool,
    code_hash: str,
    code_snippet: str,
) -> int:
    conn = pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO functions
            (file_id, name, return_type, parameters, start_line, end_line, is_prototype, code_hash, code_snippet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                file_id,
                name,
                return_type,
                parameters,
                start_line,
                end_line,
                is_prototype,
                code_hash,
                code_snippet,
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        pool.release(conn)


def insert_function_call(pool: SQLiteConnectionPool, caller_id: int, callee_id: int):
    if caller_id == callee_id:
        return
    conn = pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO function_calls (caller_id, callee_id)
            VALUES (?, ?)
        """,
            (caller_id, callee_id),
        )
        conn.commit()
    finally:
        pool.release(conn)


def insert_file_summary(
    pool: SQLiteConnectionPool,
    file_id: int,
    commit_sha: str,
    summary: str,
    summary_refined: str,
):
    conn = pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO file_summaries (file_id, commit_sha, summary, summary_refined)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(file_id, commit_sha) DO UPDATE SET
               summary=excluded.summary,
               summary_refined=excluded.summary_refined
        """,
            (file_id, commit_sha, summary, summary_refined),
        )
        conn.commit()
    finally:
        pool.release(conn)


def insert_function_summary(
    pool: SQLiteConnectionPool,
    function_id: int,
    commit_sha: str,
    summary: str,
    summary_refined: str,
):
    conn = pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO function_summaries (function_id, commit_sha, summary, summary_refined)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(function_id, commit_sha) DO UPDATE SET
               summary=excluded.summary,
               summary_refined=excluded.summary_refined
        """,
            (function_id, commit_sha, summary, summary_refined),
        )
        conn.commit()
    finally:
        pool.release(conn)


def fetch_function_name_and_file(pool: SQLiteConnectionPool, function_id: int):
    """
    Returns (unique_name, path) for the given function_id.
    'unique_name' = 'relative_path::functionName'
    """
    conn = pool.acquire()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT f.path, fn.name
              FROM functions fn
              JOIN files f ON fn.file_id = f.file_id
             WHERE fn.function_id = ?
        """,
            (function_id,),
        )
        row = cur.fetchone()
        if not row:
            return (None, None)
        rel_path, func_name = row
        unique_name = f"{rel_path}::{func_name}"
        return unique_name, rel_path
    finally:
        pool.release(conn)
