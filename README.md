# AI-Driven C Code Summarizer

This repository provides a **DB-centric** workflow for **C code analysis** and **LLM-based summarization** using Python. It collects AST information from a codebase via [Tree-sitter](https://tree-sitter.github.io/tree-sitter/), stores it in a SQLite database, and optionally generates file-level and function-level summaries using an LLM (e.g. OpenAI’s ChatCompletion). The code also tracks a **function reference graph**, letting you see which functions call or are called by others.

## Table of Contents

1. [Features](#features)  
2. [Prerequisites & Installation](#prerequisites--installation)  
3. [Repository Structure](#repository-structure)  
4. [How It Works](#how-it-works)  
5. [Usage](#usage)  
6. [Examples](#examples)  
7. [Caveats](#caveats)  
8. [License](#license)

---

## Features

- **AST-Based Parsing**  
  Uses [Tree-sitter](https://tree-sitter.github.io/tree-sitter/) to parse C files (.c, .h) into a structured tree.

- **SQLite Storage**  
  Extracted data (functions, structs, typedefs, globals, references) is stored in a local SQLite database (`summaries.db` by default).

- **Two-Level Summarization**  
  - **File-Level**: Summarizes each C file’s purpose and major components.  
  - **Function-Level**: Summarizes each function’s logic, inputs, outputs (only if `--summarize-functions` is used).

- **Reference Graph**  
  Tracks function calls (caller → callee). The final output (JSON or “pretty” Rich-based CLI) shows which functions call or are called by each function.

- **Incremental**  
  You can skip analysis (`--no-analysis`) to merely read previously stored data from the DB, or re-run partial or full summarization.

- **Duplicate-Reference Avoidance**  
  Even if a function is called multiple times from the same caller, the final output deduplicates references for clarity.

---

## Prerequisites & Installation

1. **Python 3.8+**  
   Make sure Python is installed.  

2. **Install Dependencies**  
   - [tree_sitter](https://pypi.org/project/tree_sitter/) (requires the compiled `libtree-sitter-c.so` / `.dylib` / `.dll`)  
   - [openai](https://pypi.org/project/openai/) for the LLM calls  
   - [rich](https://pypi.org/project/rich/) for pretty CLI output  
   - [PyYAML](https://pypi.org/project/PyYAML/) (if your config needs it)

   Example:
   ```bash
   pip install tree_sitter openai rich PyYAML
   ```

3. **Compile and place** `libtree-sitter-c.<extension>` in your working directory (or set the path in `parser_setup.py` accordingly).

4. **OpenAI API Key**  
   - You’ll need a `config.yaml` file or environment variables to supply `openai.api_key`.  
   - Alternatively, edit `llm_client.py` to set your key.

---

## Repository Structure

A simplified overview of the major files:

```
.
├── code_map_builder.py   # DB-based parsing & storing of AST info
├── code_extractor.py     # Tree-sitter logic to extract functions/structs/prototypes
├── db_utils.py           # SQLite schema, insert/select helpers
├── llm_client.py         # LLM (OpenAI) client
├── main.py               # Entry point: parse, store, summarize, output
├── parser_setup.py       # Tree-sitter parser creation
├── summarizer.py         # Summarize files/functions & pretty overview
├── config.yaml           # (Optional) For your OpenAI keys & model configs
└── ...
```

**Key Points**:

- **`main.py`** is the primary CLI.  
- **`db_utils.py`** sets up the SQLite schema & basic DB operations.  
- **`code_map_builder.py`** does the main parse → DB storage.  
- **`summarizer.py`** holds the actual code that queries the DB for final output, as well as file/function-level LLM calls.

---

## How It Works

1. **Tree-sitter Parsing**  
   - Each `.c` or `.h` file is parsed into an AST. We collect function definitions, prototypes, structs, globals, etc.

2. **Storing in SQLite**  
   - We store file paths, function data, references, and optional prototypes in a DB.

3. **LLM Summaries**  
   - **File-Level**: If `--no-llm` is not set, we read each file’s content + extracted info, feed it to the LLM for a short summary, then refine it.  
   - **Function-Level**: If `--summarize-functions` is used, we also generate summaries for each function’s snippet.

4. **Reference Graph**  
   - A `function_calls` table tracks `(caller_id, callee_id)` pairs.  
   - The final output merges that into “callers: [...]” and “callees: [...]” for each function.

5. **Duplicate Removal & Merging**  
   - If a function is found multiple times (prototype vs. definition), references from other files prefer the real definition if it exists.  
   - Duplicate calls from the same caller are collapsed to a single entry in the final output.

---

## Usage

1. **Basic Command**  
   ```bash
   python main.py --path /path/to/my_c_project --pretty
   ```
   - Parses & stores the code in `summaries.db`.  
   - Generates file-level summaries (unless you add `--no-llm`).  
   - Prints a Rich-based overview (file structure, function references, summaries).

2. **Skip Summaries**  
   ```bash
   python main.py --path /path/to/my_c_project --no-llm --pretty
   ```
   - Parses & stores the code but **does not** call the LLM.  
   - You’ll see references but no file-level or function-level summaries.

3. **Function Summaries**  
   ```bash
   python main.py --path /my_c_project --summarize-functions --pretty
   ```
   - Also calls the LLM for every function snippet.  
   - Displays them in the final Rich output.

4. **Only Output** (skip parsing)  
   ```bash
   python main.py --path /my_c_project --no-analysis --json
   ```
   - Does not parse or summarize.  
   - Just pulls existing data from `summaries.db` and prints JSON.  
   - Useful if you want to re-check or display the results of a previous run.

5. **JSON vs. Pretty**  
   - `--json` prints a JSON structure with all the data.  
   - `--pretty` prints a Rich-based tree and table display.

6. **Database**  
   - By default, everything goes into `summaries.db`. Remove or rename it if you want a fresh parse.

---

## Examples

- **Generate file-level summaries** for your code, print in Rich format:
  ```bash
  python main.py --path /my/c/project --pretty
  ```
- **Also** do function-level summarization:
  ```bash
  python main.py --path /my/c/project --summarize-functions --pretty
  ```
- **View** the final results in JSON:
  ```bash
  python main.py --path /my/c/project --no-analysis --json
  ```

---

## Caveats

1. **Function Name Collisions**: If two different `.c` files define the same function name, references from external files might unify them unless you add more robust logic (like fully qualifying them by file scope).  
2. **Large Projects**: Summaries can be expensive for hundreds or thousands of files/functions. Consider partial usage or incremental diffs if cost or time is a concern.  
3. **Prototype vs. Definition**: This code tries to unify references to the real definition if it exists. If you have unusual code structures or multiple definitions, references might still need manual inspection.  
4. **OpenAI Key**: Make sure you have `OPENAI_API_KEY` set or your config is placed in `config.yaml`.  
5. **Security**: The LLM calls send code snippets to an external API (OpenAI). Keep that in mind for proprietary code.


---

### Enjoy AI-Driven Summaries!

Questions or issues? Feel free to open an issue or contact the maintainers. We hope this workflow helps you better understand and maintain your C codebases with the power of an LLM-based summarizer!
```
