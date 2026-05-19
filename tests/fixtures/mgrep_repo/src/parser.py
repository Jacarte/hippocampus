from __future__ import annotations


class TokenParser:
    def __init__(self, delimiter: str = " ") -> None:
        self.delimiter = delimiter
        self._cache: dict[str, list[str]] = {}

    def parse(self, text: str) -> list[str]:
        if text in self._cache:
            return self._cache[text]
        tokens = [t for t in text.split(self.delimiter) if t]
        self._cache[text] = tokens
        return tokens

    def clear_cache(self) -> None:
        self._cache.clear()


def normalise_whitespace(text: str) -> str:
    import re

    return re.sub(r"\s+", " ", text).strip()


def count_tokens(text: str, delimiter: str = " ") -> int:
    return len(TokenParser(delimiter).parse(text))
