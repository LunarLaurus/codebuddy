# parser_setup.py
import asyncio
from typing import Optional

# Use prebuilt languages
from tree_sitter_languages import get_language
from tree_sitter import Parser

# ------------------------------
# Global caches
# ------------------------------
_PARSER_CACHE: dict[str, Parser] = {}
_LANGUAGE_CACHE: dict[str, "Language"] = {}


# ------------------------------
# Language loader
# ------------------------------
def load_language(name: str = "c") -> "Language":
    """
    Load a Tree-sitter language from prebuilt languages (tree_sitter_languages).
    Caches the language object.
    """
    if name in _LANGUAGE_CACHE:
        return _LANGUAGE_CACHE[name]

    lang = get_language(name)
    _LANGUAGE_CACHE[name] = lang
    return lang


# ------------------------------
# Parser factory
# ------------------------------
def get_parser(language: "Language") -> Parser:
    """
    Return a cached Parser for a given language.
    """
    lang_name = language.name
    if lang_name in _PARSER_CACHE:
        return _PARSER_CACHE[lang_name]

    parser = Parser()
    parser.set_language(language)
    _PARSER_CACHE[lang_name] = parser
    return parser


# ------------------------------
# Sync file parse
# ------------------------------
def parse_file(parser: Parser, file_path: str) -> tuple["Tree", str]:
    """
    Parse a file and return (tree, source_code)
    """
    with open(file_path, "r", encoding="utf-8") as f:
        code = f.read()
    tree = parser.parse(bytes(code, "utf-8"))
    return tree, code


# ------------------------------
# Async helper
# ------------------------------
async def parse_file_async(parser: Parser, file_path: str) -> tuple["Tree", str]:
    """
    Async wrapper to parse a file.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, parse_file, parser, file_path)


# ------------------------------
# Multi-language loader convenience
# ------------------------------
def load_parsers_for_languages(names: list[str]) -> dict[str, Parser]:
    """
    Load multiple parsers at once, returns dict language_name -> Parser
    """
    return {name: get_parser(load_language(name)) for name in names}
