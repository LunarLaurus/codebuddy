# db_utils.py

import sqlite3
import os
import hashlib

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS file_symbols (
    symbol_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    symbol_type TEXT NOT NULL,  -- e.g. "struct", "typedef", "global"
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

def get_connection(db_path="summaries.db"):
    must_init = not os.path.exists(db_path)
    conn = sqlite3.connect(db_path)
    if must_init:
        conn.executescript(SCHEMA)
        conn.commit()
    return conn

def compute_code_hash(code_text):
    return hashlib.md5(code_text.encode('utf-8')).hexdigest()

def insert_or_get_file_id(conn, path):
    """
    Insert the *relative* file path if not present, return file_id.
    """
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO files (path) VALUES (?)", (path,))
    conn.commit()
    cur.execute("SELECT file_id FROM files WHERE path = ?", (path,))
    row = cur.fetchone()
    return row[0] if row else None

def insert_file_symbol(conn, file_id, symbol_type, symbol_name, code_snippet):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO file_symbols (file_id, symbol_type, symbol_name, code_snippet)
        VALUES (?, ?, ?, ?)
    """, (file_id, symbol_type, symbol_name, code_snippet))
    conn.commit()

def insert_function(conn, file_id, name, return_type, parameters,
                    start_line, end_line, is_prototype, code_hash, code_snippet):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO functions
        (file_id, name, return_type, parameters,
         start_line, end_line, is_prototype, code_hash, code_snippet)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (file_id, name, return_type, parameters,
          start_line, end_line, is_prototype, code_hash, code_snippet))
    conn.commit()
    return cur.lastrowid

def insert_function_call(conn, caller_id, callee_id):
    if caller_id == callee_id:
        return
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO function_calls (caller_id, callee_id)
        VALUES (?, ?)
    """, (caller_id, callee_id))
    conn.commit()

def insert_file_summary(conn, file_id, commit_sha, summary, summary_refined):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO file_summaries (file_id, commit_sha, summary, summary_refined)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(file_id, commit_sha) DO UPDATE SET
           summary=excluded.summary,
           summary_refined=excluded.summary_refined
    """, (file_id, commit_sha, summary, summary_refined))
    conn.commit()

def insert_function_summary(conn, function_id, commit_sha, summary, summary_refined):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO function_summaries (function_id, commit_sha, summary, summary_refined)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(function_id, commit_sha) DO UPDATE SET
           summary=excluded.summary,
           summary_refined=excluded.summary_refined
    """, (function_id, commit_sha, summary, summary_refined))
    conn.commit()

def fetch_function_name_and_file(conn, function_id):
    """
    Returns (unique_name, path) for the given function_id.
    'unique_name' = 'relative_path::functionName'
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT f.path, fn.name
          FROM functions fn
          JOIN files f ON fn.file_id = f.file_id
         WHERE fn.function_id = ?
    """, (function_id,))
    row = cur.fetchone()
    if not row:
        return (None, None)
    rel_path, func_name = row
    unique_name = f"{rel_path}::{func_name}"
    return (unique_name, rel_path)
