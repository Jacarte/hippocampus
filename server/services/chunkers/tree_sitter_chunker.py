from __future__ import annotations

import uuid
from typing import Any

from tree_sitter_languages import get_language, get_parser

_LANGUAGE_MAP: dict[str, str] = {
    "go": "go",
    "javascript": "javascript",
    "typescript": "typescript",
    "rust": "rust",
    "java": "java",
    "c": "c",
    "cpp": "cpp",
    "ruby": "ruby",
}

_QUERY_MAP: dict[str, str] = {
    "go": """
        (function_declaration name: (identifier) @function)
        (method_declaration name: (field_identifier) @method)
        (type_declaration (type_spec name: (type_identifier) @struct
            type: (struct_type)))
        (type_declaration (type_spec name: (type_identifier) @interface
            type: (interface_type)))
        (type_declaration (type_alias name: (type_identifier) @type_alias))
    """,
    "javascript": """
        (function_declaration name: (identifier) @function)
        (class_declaration name: (identifier) @class)
    """,
    "typescript": """
        (function_declaration name: (identifier) @function)
        (class_declaration name: (type_identifier) @class)
        (interface_declaration name: (type_identifier) @interface)
        (type_alias_declaration name: (type_identifier) @type_alias)
        (enum_declaration name: (identifier) @enum)
    """,
    "rust": """
        (function_item name: (identifier) @function)
        (impl_item body: (declaration_list (function_item name: (identifier) @method)))
        (struct_item name: (type_identifier) @struct)
        (enum_item name: (type_identifier) @enum)
        (trait_item name: (type_identifier) @trait)
        (type_item name: (type_identifier) @type_alias)
    """,
    "java": """
        (class_declaration name: (identifier) @class)
        (interface_declaration name: (identifier) @interface)
        (enum_declaration name: (identifier) @enum)
        (method_declaration name: (identifier) @function)
    """,
    "c": """
        (function_definition declarator: (function_declarator
            declarator: (identifier) @function))
        (type_definition declarator: (type_identifier) @type_alias)
    """,
    "cpp": """
        (function_definition declarator: (function_declarator
            declarator: (identifier) @function))
        (class_specifier name: (type_identifier) @class)
        (struct_specifier name: (type_identifier) @struct)
        (enum_specifier name: (type_identifier) @enum)
        (type_definition declarator: (type_identifier) @type_alias)
    """,
    "ruby": """
        (method name: (identifier) @function)
        (class name: (constant) @class)
        (module name: (constant) @module)
    """,
}


class TreeSitterChunker:
    """Extracts symbol-level chunks from source files using tree-sitter AST parsing.

    Raises on any parse or grammar error — does not silently fall back.
    """

    def chunk(self, file_path: str, content: str, language: str) -> list[dict[str, Any]]:
        """Parse *content* and return one chunk per matched symbol.

        Args:
            file_path: Original file path (stored in each chunk, not read from disk).
            content: Source code as a string.
            language: Language name matching keys in ``_LANGUAGE_MAP``.

        Returns:
            List of chunk dicts with keys: id, file_path, language, symbol_name,
            symbol_kind, line_start, line_end, content, score.

        Raises:
            KeyError: If *language* is not in ``_LANGUAGE_MAP``.
            Exception: Any tree-sitter parse or query error propagates unchanged.
        """
        ts_lang_key = _LANGUAGE_MAP[language]
        ts_language = get_language(ts_lang_key)
        parser = get_parser(ts_lang_key)

        tree = parser.parse(content.encode("utf-8"))
        query = ts_language.query(_QUERY_MAP[language])
        captures = query.captures(tree.root_node)

        lines = content.splitlines(keepends=True)
        chunks: list[dict[str, Any]] = []

        for node, capture_name in captures:
            symbol_name = content[node.start_byte:node.end_byte]
            symbol_kind = capture_name

            body_node = _find_body_node(node)
            line_start = body_node.start_point[0] + 1
            line_end = body_node.end_point[0] + 1

            chunks.append({
                "id": str(uuid.uuid4()),
                "file_path": file_path,
                "language": language,
                "symbol_name": symbol_name,
                "symbol_kind": symbol_kind,
                "line_start": line_start,
                "line_end": line_end,
                "content": "".join(lines[line_start - 1:line_end]),
                "score": 0.0,
            })

        return chunks


def _find_body_node(name_node: Any) -> Any:
    """Walk up from a name-capture node to find the enclosing declaration node."""
    node = name_node.parent
    if node is None:
        return name_node
    # For Go type declarations: walk past type_spec / type_alias to type_declaration
    if node.type in ("type_spec", "type_alias"):
        parent = node.parent
        if parent is not None:
            return parent
    # For C/C++ functions: identifier is inside function_declarator inside function_definition
    if node.type == "function_declarator":
        parent = node.parent
        if parent is not None:
            return parent
    return node
