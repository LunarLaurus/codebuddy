import os
from tree_sitter import Language, Parser

def load_language():
    so_path = os.path.abspath("libtree-sitter-c.so")
    if not os.path.exists(so_path):
        raise FileNotFoundError(f"Could not find Tree-sitter library at {so_path}")

    LANG_C = Language(so_path, 'c')
    return LANG_C

def create_parser(language):
    parser = Parser()
    parser.set_language(language)
    return parser
