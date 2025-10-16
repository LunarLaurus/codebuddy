# code_analysis/parser.py
import asyncio
from pathlib import Path
from tree_sitter_languages import get_language
from tree_sitter import Parser

_PARSER_CACHE = {}
_LANGUAGE_CACHE = {}


def load_language(name="c"):
    if name in _LANGUAGE_CACHE:
        return _LANGUAGE_CACHE[name]
    lang = get_language(name)
    _LANGUAGE_CACHE[name] = lang
    return lang


def get_parser(language):
    if language.name in _PARSER_CACHE:
        return _PARSER_CACHE[language.name]
    parser = Parser()
    parser.set_language(language)
    _PARSER_CACHE[language.name] = parser
    return parser


def parse_file(parser, path: Path):
    code = path.read_text(encoding="utf-8")
    tree = parser.parse(bytes(code, "utf-8"))
    return tree, code


async def parse_file_async(parser, path: Path):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, parse_file, parser, path)
