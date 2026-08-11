#!/usr/bin/env python3
"""Generate the editor syntax grammars.

Generated rather than hand-written for the same reason MODEL-SPEC.md is: a
keyword list maintained by hand drifts from the parser, and highlighting that
disagrees with the parser is worse than none. It tells the reader a word is
structural when it is not.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from frostlang import __version__
from frostlang.parser import (HARD_WORDS, CHUNK_SINGULAR, CHUNK_PLURAL,
                              ORDINALS, TRANSFORMS, AGGREGATES, TIME_UNITS)

OUT = os.path.join(HERE, "editors")

# Keywords that open or close a block, highlighted apart from the rest so the
# structure of a long script is visible at a glance.
BLOCK_WORDS = ["if", "then", "else", "end", "repeat", "pipe", "to", "ensure",
               "while", "until", "forever", "for", "each"]

CONTROL_WORDS = ["exit", "next", "return", "quit", "try"]


def alternation(words):
    """A regex alternation, longest first so `is not` beats `is`."""
    return "|".join(sorted((w for w in words), key=len, reverse=True))


def tmlanguage():
    keywords = sorted(HARD_WORDS - set(BLOCK_WORDS) - set(CONTROL_WORDS))
    nouns = sorted(set(CHUNK_SINGULAR) | set(CHUNK_PLURAL))
    ordinals = sorted(set(ORDINALS) | {"last", "middle", "any"})
    functions = sorted(set(TRANSFORMS) | set(AGGREGATES))
    units = sorted(TIME_UNITS)

    return {
        "$schema": "https://raw.githubusercontent.com/martinring/tmlanguage/"
                   "master/tmlanguage.json",
        "name": "frost",
        "scopeName": "source.frost",
        "fileTypes": ["frost"],
        "patterns": [{"include": f"#{n}"} for n in (
            "comment", "string", "number", "block", "control", "keyword",
            "function", "ordinal", "noun", "unit", "special")],
        "repository": {
            "comment": {
                "patterns": [
                    {"name": "comment.line.double-dash.frost",
                     "match": "--.*$"},
                    {"name": "comment.line.number-sign.frost",
                     "match": "(?<!\\S)#.*$"},
                ]
            },
            "string": {
                "name": "string.quoted.double.frost",
                "begin": "\"",
                "end": "\"",
                "patterns": [{"name": "constant.character.escape.frost",
                              "match": "\\\\."}],
            },
            "number": {
                "name": "constant.numeric.frost",
                "match": "\\b\\d+(\\.\\d+)?\\b",
            },
            "block": {
                "name": "keyword.control.block.frost",
                "match": f"\\b({alternation(BLOCK_WORDS)})\\b",
            },
            "control": {
                "name": "keyword.control.flow.frost",
                "match": f"\\b({alternation(CONTROL_WORDS)})\\b",
            },
            "keyword": {
                "name": "keyword.other.frost",
                "match": f"\\b({alternation(keywords)})\\b",
            },
            # Contextual words: highlighted only where the parser treats them
            # as structural, so `line count` stays a plain name.
            "function": {
                "name": "support.function.frost",
                "match": f"(?<=\\bthe\\s)({alternation(functions)})\\b",
            },
            "ordinal": {
                "name": "constant.language.ordinal.frost",
                "match": f"\\b({alternation(ordinals)})\\s+"
                         f"(?=({alternation(nouns)})\\b)",
            },
            "noun": {
                "name": "entity.name.type.chunk.frost",
                "match": f"\\b({alternation(nouns)})\\b(?=\\s+(\\d|of\\b|-))",
            },
            "unit": {
                "name": "keyword.other.unit.frost",
                "match": f"(?<=\\d\\s)({alternation(units)})\\b",
            },
            "special": {
                "name": "variable.language.frost",
                "match": "(?<=\\bthe\\s)(result|arguments|whole|matches|"
                         "environment|current|length|number|standard|global|"
                         "empty)\\b",
            },
        },
    }


PACKAGE_JSON = {
    "name": "frost-language",
    "displayName": "frost",
    "description": "Syntax highlighting for the frost scripting language",
    "version": __version__,
    "engines": {"vscode": "^1.60.0"},
    "categories": ["Programming Languages"],
    "contributes": {
        "languages": [{
            "id": "frost",
            "aliases": ["frost"],
            "extensions": [".frost"],
            "configuration": "./language-configuration.json",
        }],
        "grammars": [{
            "language": "frost",
            "scopeName": "source.frost",
            "path": "./frost.tmLanguage.json",
        }],
    },
}


def language_configuration():
    """Indent rules taken from the formatter rather than written again.

    An editor that indents differently from `--format` fights the author on
    every line, and the two rules drifting apart is exactly what happens when
    the same regex is maintained in two files.
    """
    from frostlang.formatter import INDENT_AFTER, DEDENT_BEFORE
    return {
        "comments": {"lineComment": "--"},
        "brackets": [["(", ")"]],
        "autoClosingPairs": [
            {"open": "(", "close": ")"},
            {"open": "\"", "close": "\"", "notIn": ["string"]},
        ],
        "indentationRules": {
            "increaseIndentPattern": "^\\s*" + INDENT_AFTER.pattern.lstrip("^"),
            "decreaseIndentPattern": "^\\s*" + DEDENT_BEFORE.pattern.lstrip("^"),
        },
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    files = {
        "frost.tmLanguage.json": tmlanguage(),
        "package.json": PACKAGE_JSON,
        "language-configuration.json": language_configuration(),
    }
    for name, data in files.items():
        with open(os.path.join(OUT, name), "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
    print(f"wrote {len(files)} files to editors/ "
          f"({len(HARD_WORDS)} reserved words)")


if __name__ == "__main__":
    main()
