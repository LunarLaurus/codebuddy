# parser.py
import asyncio
from pathlib import Path
from typing import Dict, Tuple, Union
from tree_sitter_language_pack import get_language
from tree_sitter import Parser, Tree

# ------------------------------
# Caches
# ------------------------------
_PARSER_CACHE: Dict[str, Parser] = {}
_LANGUAGE_CACHE: Dict[str, object] = {}


# ------------------------------
# Language loader
# ------------------------------
def load_language(name: str = "c"):
    """
    Load a Tree-sitter language with caching.
    """
    key = name.lower()
    if key in _LANGUAGE_CACHE:
        return _LANGUAGE_CACHE[key]

    lang = get_language(key)  # returns tree_sitter.Language
    _LANGUAGE_CACHE[key] = lang
    return lang


# ------------------------------
# Parser getter
# ------------------------------
def get_parser(language: object) -> Parser:
    """
    Return a cached Parser instance for a given Language object.
    """
    lang_name = getattr(language, "name", str(language))
    if lang_name in _PARSER_CACHE:
        return _PARSER_CACHE[lang_name]

    parser = Parser(language)  # <-- pass Language directly to constructor
    _PARSER_CACHE[lang_name] = parser
    return parser


# ------------------------------
# File parsing
# ------------------------------
def parse_file(parser: Parser, path: Union[str, Path]) -> Tuple[Tree, str]:
    if isinstance(path, str):
        path = Path(path)
    code = path.read_text(encoding="utf-8")
    tree = parser.parse(bytes(code, "utf-8"))
    return tree, code


async def parse_file_async(parser: Parser, path: Union[str, Path]) -> Tuple[Tree, str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, parse_file, parser, path)


# ------------------------------
# Multi-language parser loader
# ------------------------------
def load_parsers_for_languages(names: list[str]) -> Dict[str, Parser]:
    return {name: get_parser(load_language(name)) for name in names}
