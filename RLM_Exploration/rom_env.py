from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

FRONTMATTER_BOUNDARY = "---"


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_BOUNDARY:
        return {}, text

    metadata: Dict[str, str] = {}
    idx = 1
    while idx < len(lines) and lines[idx].strip() != FRONTMATTER_BOUNDARY:
        line = lines[idx].strip()
        if not line or line.startswith("#"):
            idx += 1
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = _strip_quotes(value)
        idx += 1

    body = "\n".join(lines[idx + 1 :]) if idx < len(lines) else text
    return metadata, body


def build_index(corpus_dir: Path) -> List[Dict[str, str]]:
    index: List[Dict[str, str]] = []
    for path in sorted(corpus_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8", errors="ignore")
        metadata, _ = parse_frontmatter(content)
        index.append(
            {
                "path": str(path),
                "title": metadata.get("title", path.name),
                "source": metadata.get("source", ""),
                "date": metadata.get("date", "unknown"),
                "description": metadata.get("description", ""),
                "word_count": metadata.get("word_count", ""),
            }
        )
    return index


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


class CorpusEnv:
    def __init__(self, corpus_dir: Path):
        self.corpus_dir = Path(corpus_dir)
        self.index = build_index(self.corpus_dir)
        self._cache: Dict[str, str] = {}
        self._body_cache: Dict[str, str] = {}
        self._meta_by_path = {item["path"]: item for item in self.index}

    def _resolve_path(self, path: str) -> Path | None:
        if not path:
            return None
        if path in self._meta_by_path:
            return Path(path)

        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = (self.corpus_dir / candidate).resolve()
        if candidate.exists():
            return candidate

        name = Path(path).name
        matches = [Path(item["path"]) for item in self.index if Path(item["path"]).name == name]
        if len(matches) == 1:
            return matches[0]
        return None

    def resolve_path(self, path: str) -> str | None:
        resolved = self._resolve_path(path)
        return str(resolved) if resolved else None

    def _get_body(self, path: str) -> str:
        if path in self._body_cache:
            return self._body_cache[path]
        if path not in self._cache:
            self._cache[path] = Path(path).read_text(encoding="utf-8", errors="ignore")
        _, body = parse_frontmatter(self._cache[path])
        self._body_cache[path] = body
        return body

    def list_files(self, limit: int | None = None) -> List[Dict[str, str]]:
        return self.index[:limit] if limit else self.index

    def get_metadata(self, path: str) -> Dict[str, str]:
        resolved = self._resolve_path(path)
        if not resolved:
            return {}
        return self._meta_by_path.get(str(resolved), {})

    def read_file(self, path: str, max_chars: int = 8000, offset: int = 0) -> Dict[str, str]:
        resolved = self._resolve_path(path)
        if not resolved:
            return {"error": f"file_not_found: {path}", "path": path}

        resolved_path = str(resolved)
        if resolved_path not in self._cache:
            text = resolved.read_text(encoding="utf-8", errors="ignore")
            self._cache[resolved_path] = text
        metadata, body = parse_frontmatter(self._cache[resolved_path])
        self._body_cache[resolved_path] = body
        if offset:
            body = body[offset:]
        return {
            "path": resolved_path,
            "metadata": metadata,
            "body": _truncate(body, max_chars),
        }

    def search(
        self,
        query: str,
        limit: int = 5,
        snippet_window: int = 400,
        max_per_file: int = 2,
    ) -> List[Dict[str, str]]:
        def run_search(pattern: re.Pattern[str]) -> List[Dict[str, str]]:
            found: List[Dict[str, str]] = []
            per_file: Dict[str, int] = {}
            for item in self.index:
                path = item["path"]
                content = self._get_body(path)
                file_hits = per_file.get(path, 0)
                for match in pattern.finditer(content):
                    if file_hits >= max_per_file:
                        break
                    start = max(match.start() - snippet_window // 2, 0)
                    end = min(match.end() + snippet_window // 2, len(content))
                    snippet = content[start:end].replace("\n", " ")
                    found.append(
                        {
                            "path": path,
                            "title": item.get("title", ""),
                            "date": item.get("date", "unknown"),
                            "source": item.get("source", ""),
                            "snippet": _truncate(snippet, snippet_window),
                        }
                    )
                    file_hits += 1
                    per_file[path] = file_hits
                    if len(found) >= limit:
                        return found
            return found

        if not query:
            return []

        max_per_file = max(1, min(max_per_file, limit))

        phrase_pattern = re.compile(re.escape(query), re.IGNORECASE)
        matches = run_search(phrase_pattern)
        if matches:
            return matches

        tokens = [token for token in re.split(r"\W+", query) if len(token) > 2]
        if not tokens:
            return []
        token_pattern = re.compile(r"(?:" + "|".join(map(re.escape, tokens)) + r")", re.IGNORECASE)
        return run_search(token_pattern)

    def execute_action(self, action: Dict[str, object], defaults: Dict[str, int]) -> Dict[str, object]:
        action_name = str(action.get("action", ""))
        args = action.get("args", {}) or {}

        if action_name == "list_files":
            limit = int(args.get("limit", defaults.get("max_search_results", 6)))
            return {"files": self.list_files(limit=limit)}
        if action_name == "get_metadata":
            return {"metadata": self.get_metadata(str(args.get("path", "")))}
        if action_name == "read_file":
            max_chars = int(args.get("max_chars", defaults.get("max_read_chars", 8000)))
            offset = int(args.get("offset", 0))
            return self.read_file(str(args.get("path", "")), max_chars=max_chars, offset=offset)
        if action_name == "search":
            query = str(args.get("query", ""))
            limit = int(args.get("limit", defaults.get("max_search_results", 6)))
            window = int(args.get("snippet_window", defaults.get("snippet_window", 400)))
            max_per_file = int(args.get("max_per_file", defaults.get("max_matches_per_file", 2)))
            return {
                "matches": self.search(
                    query=query,
                    limit=limit,
                    snippet_window=window,
                    max_per_file=max_per_file,
                )
            }

        return {"error": f"Unknown action: {action_name}"}
