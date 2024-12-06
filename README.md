# codebuddy

This tool parses a C codebase using Tree-sitter, extracts information about functions, and provides a structured overview of the code. It uses OpenAI's API for LLM descriptions.

## Prerequisites

- Python 3.7+
- `gcc` or `clang` to compile the Tree-sitter grammar
- The `tree_sitter` Python package

## Steps to Set Up

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt

2. **Compile the Tree-Sitter-Grammar:**
   ```bash
   git clone https://github.com/tree-sitter/tree-sitter-c
   cd tree-sitter-c
   gcc -shared -fPIC src/parser.c -o ../libtree-sitter-c.so
   cd ..
   
3. **Put your API key in config.yaml**


4. **Run the Tool:**
    ```bash
   python src/main.py --path path/to/your/c/codebase

This will print a codebase overview. 

Flags:  

--only-first-llm (just doest one llm pass on descriptions)

--no-llm (no llm descriptions)

--pretty (make it pretty with Rich)

--json (json output)
