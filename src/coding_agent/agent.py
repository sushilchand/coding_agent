from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from groq import Groq

STOP_WORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "with",
    "into",
    "from",
    "this",
    "that",
    "these",
    "those",
    "there",
    "where",
    "when",
    "what",
    "why",
    "how",
    "is",
    "are",
    "be",
    "been",
    "being",
    "it",
    "its",
    "of",
    "in",
    "on",
    "to",
    "at",
    "by",
    "as",
}

DEFAULT_EXTENSIONS = {".py"}

BUG_FIX_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_repository",
            "description": "Find relevant Python source files, excluding virtual environments and internal directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_bug_lines",
            "description": "Inspect relevant lines and surrounding context in a Python source file inside the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "query": {"type": "string"},
                    "context_lines": {"type": "integer"},
                },
                "required": ["file_path", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_file",
            "description": "Apply a targeted replacement in an inspected Python source file inside the repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_snippet": {"type": "string"},
                    "new_snippet": {"type": "string"},
                },
                "required": ["file_path", "old_snippet", "new_snippet"],
            },
        },
    },
]


def debug_log(message: str) -> None:
    try:
        print(f"[debug] {message}")
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        raise SystemExit(0)


def load_environment_from_file(repo_root: str | Path = ".") -> None:
    root = Path(repo_root).resolve()
    env_file = root / ".env"
    if not env_file.exists():
        return

    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class GroqLLMClient:
    """Small Groq-backed LLM wrapper for plain-English bug analysis."""

    def __init__(self, repo_root: str | Path = "."):
        self.repo_root = Path(repo_root).resolve()
        load_environment_from_file(self.repo_root)

    def request_tool_call(self, messages: list[dict[str, Any]]) -> Any:
        client = Groq(api_key=os.environ["GROQ_API_KEY"])
        model = os.getenv("LLM_MODEL_NAME", "openai/gpt-oss-120b")
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=BUG_FIX_TOOLS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=800,
            )
            return completion.choices[0].message
        except Exception as exc:  # pragma: no cover - runtime dependency guard
            debug_log(f"Groq LLM call failed: {exc}")
            if "not in request.tools" not in str(exc):
                return None

        retry_messages = messages + [
            {
                "role": "user",
                "content": (
                    "Use only the tools declared in this request: search_repository, find_bug_lines, "
                    "and replace_file. Do not call repo_browser.open_file or any other tool."
                ),
            }
        ]
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=retry_messages,
                tools=BUG_FIX_TOOLS,
                tool_choice="required",
                temperature=0.2,
                max_tokens=800,
            )
            return completion.choices[0].message
        except Exception as exc:  # pragma: no cover - runtime dependency guard
            debug_log(f"Groq constrained tool retry failed: {exc}")
            return None


class RepositorySearchTool:
    """Search a repository for files related to a bug description."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        debug_log(
            f"RepositorySearchTool.search starting for query={query!r}, limit={limit}"
        )
        if self._should_skip_path(self.root):
            debug_log(
                f"Repository root is an internal directory; skipping search: {self.root}"
            )
            return []
        if not query or not query.strip():
            debug_log("Empty query received; returning no matches.")
            return []

        query_tokens = self._extract_tokens(query)
        debug_log(f"Extracted search tokens: {query_tokens}")
        if not query_tokens:
            debug_log("No searchable tokens found after filtering stop words.")
            return []

        hits: list[dict[str, Any]] = []
        for current_root, directories, filenames in os.walk(self.root):
            current_path = Path(current_root)
            directories[:] = [
                directory
                for directory in directories
                if not self._should_skip_path(current_path / directory)
            ]

            for filename in filenames:
                path = current_path / filename
                if path.suffix.lower() not in DEFAULT_EXTENSIONS:
                    continue

                try:
                    text = path.read_text(encoding="utf-8", errors="ignore")
                except OSError as exc:
                    debug_log(f"Could not read file {path}: {exc}")
                    continue

                lower_text = text.lower()
                score = 0
                token_matches: list[str] = []
                for token in query_tokens:
                    count = lower_text.count(token)
                    if count:
                        score += count
                        token_matches.append(token)

                if score > 0:
                    hit = {
                        "path": str(path.relative_to(self.root)),
                        "score": score,
                        "matches": token_matches,
                    }
                    hits.append(hit)
                    debug_log(
                        f"Match found in {hit['path']} with score={score}, tokens={token_matches}"
                    )

        hits.sort(key=lambda item: (-item["score"], item["path"]))
        final_hits = hits[:limit]
        debug_log(
            f"Repository search completed with {len(final_hits)} relevant file(s)."
        )
        return final_hits

    @staticmethod
    def _extract_tokens(query: str) -> list[str]:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", query.lower())
        filtered = [token for token in tokens if token not in STOP_WORDS]
        expansions: list[str] = []
        for token in filtered:
            expansions.append(token)
            if token.endswith("tion"):
                expansions.append(token[:-3])
            if token.endswith("ing"):
                expansions.append(token[:-3])
            if token in {"broken", "bug", "wrong", "error", "fail", "failed"}:
                expansions.extend(["bug", "wrong", "error", "fail", "broken"])
            if token in {"operation", "function", "method"}:
                expansions.extend(["operation", "function", "method"])

        result = []
        seen: set[str] = set()
        for token in expansions:
            if token and token not in seen:
                result.append(token)
                seen.add(token)

        debug_log(
            f"_extract_tokens: raw={tokens}, filtered={filtered}, expanded={result}"
        )
        return result

    @staticmethod
    def _should_skip_path(path: Path) -> bool:
        skip_fragments = (".git", ".venv", "__pycache__", "node_modules", ".mypy_cache")
        result = any(fragment in path.parts for fragment in skip_fragments)
        if result:
            debug_log(f"_should_skip_path returning True for {path}")
        return result


class BugLocatorTool:
    """Identify lines in a file that are most relevant to a bug description."""

    def __init__(self, context_lines: int = 2):
        self.context_lines = context_lines

    def locate(self, file_path: str | Path, query: str) -> list[dict[str, Any]]:
        path = Path(file_path)
        debug_log(f"BugLocatorTool.locate starting for file={path} and query={query!r}")
        if not path.exists():
            debug_log(f"File not found: {path}")
            return []

        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError as exc:
            debug_log(f"Could not read {path}: {exc}")
            return []

        query_tokens = RepositorySearchTool._extract_tokens(query)
        debug_log(f"Bug locator query tokens: {query_tokens}")
        if not query_tokens:
            debug_log("No tokens available for locating bug lines.")
            return []

        matches: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            lowered = line.lower()
            if any(token in lowered for token in query_tokens):
                start = max(1, index - self.context_lines)
                end = min(len(lines), index + self.context_lines)
                context = "\n".join(
                    f"{i}: {lines[i - 1]}" for i in range(start, end + 1)
                )
                match = {
                    "line_number": index,
                    "line": line.strip(),
                    "context": context,
                }
                matches.append(match)
                debug_log(f"Relevant line found at {index}: {line.strip()}")

        debug_log(f"Bug locator completed with {len(matches)} match(es).")
        return matches


class FileReplacementTool:
    """Replace a known snippet in a file with a new snippet."""

    def replace(
        self, file_path: str | Path, old_snippet: str, new_snippet: str
    ) -> bool:
        path = Path(file_path)
        debug_log(f"FileReplacementTool.replace called for {path}")
        if not path.exists():
            debug_log(f"Replacement failed: file does not exist at {path}")
            raise FileNotFoundError(f"File does not exist: {path}")

        text = path.read_text(encoding="utf-8", errors="ignore")
        if old_snippet not in text:
            debug_log("Replacement failed: old snippet not found in file.")
            return False

        updated = text.replace(old_snippet, new_snippet, 1)
        path.write_text(updated, encoding="utf-8")
        debug_log(f"Replacement succeeded for {path}")
        return True


class BugFixAgent:
    """Simple repository-aware bug search and repair workflow."""

    def __init__(self, repo_root: str | Path = "."):
        self.repo_root = Path(repo_root).resolve()
        load_environment_from_file(self.repo_root)
        self.llm_client = GroqLLMClient(self.repo_root)

    def run(self, bug_description: str, apply_patch: bool = False) -> dict[str, Any]:
        debug_log(
            f"BugFixAgent.run started with description={bug_description!r}, apply_patch={apply_patch}"
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are a repository debugging agent. Diagnose the user's reported bug from repository evidence. "
                    "You may call only these exact tools: search_repository, find_bug_lines, and replace_file. "
                    "Never call repo_browser.open_file or any tool not listed above. "
                    "Use search_repository to find relevant files, then find_bug_lines to inspect relevant code and context. "
                    "When patch mode is enabled, decide the fix yourself and use replace_file to apply a targeted change. "
                    "Never guess a replacement without inspecting the relevant code. If the evidence is insufficient, "
                    "explain what is missing and do not modify files."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Bug report: {bug_description}\n"
                    f"Patch mode: {'enabled' if apply_patch else 'disabled'}.\n"
                    f"Repository root: {self.repo_root}"
                ),
            },
        ]
        files: dict[str, dict[str, Any]] = {}
        locations: list[dict[str, Any]] = []
        patches: list[dict[str, Any]] = []
        analysis = ""

        for _ in range(8):
            message = self.llm_client.request_tool_call(messages)
            time.sleep(2)
            if message is None:
                return {
                    "status": "llm_error",
                    "files": list(files),
                    "locations": locations,
                    "llm_analysis": "The LLM request failed.",
                }

            analysis = (message.content or "").strip()
            tool_calls = message.tool_calls or []
            if not tool_calls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )
            for call in tool_calls:
                name = call.function.name
                debug_log(f"LLM requested tool: {name}")
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                    if not isinstance(arguments, dict):
                        raise ValueError("Tool arguments must be a JSON object.")
                    tool_result = self._execute_tool(name, arguments, apply_patch)
                except (json.JSONDecodeError, ValueError, KeyError, OSError) as exc:
                    debug_log(f"Tool call {name} failed: {exc}")
                    tool_result = {"error": str(exc)}

                if name == "search_repository" and isinstance(tool_result, list):
                    files.update({entry["path"]: entry for entry in tool_result})
                elif name == "find_bug_lines" and isinstance(tool_result, list):
                    locations.extend(tool_result)
                elif (
                    name == "replace_file"
                    and isinstance(tool_result, dict)
                    and tool_result.get("replaced")
                ):
                    patches.append(tool_result)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": json.dumps(tool_result, ensure_ascii=True),
                    }
                )
        else:
            analysis = "Stopped after the maximum number of LLM tool-call rounds."

        debug_log("BugFixAgent.run finished after LLM tool selection.")
        return {
            "status": "matches_found" if files or locations else "no_matches",
            "files": list(files),
            "locations": locations,
            "llm_analysis": analysis,
            "patched": bool(patches),
            "patches": patches,
        }

    def _execute_tool(
        self, name: str, arguments: dict[str, Any], apply_patch: bool
    ) -> Any:
        if name == "search_repository":
            query = str(arguments["query"])
            limit = max(1, min(int(arguments.get("limit", 10)), 50))
            return search_repository(query, self.repo_root, limit)

        if name in {"find_bug_lines", "replace_file"}:
            requested_path = Path(str(arguments["file_path"]))
            file_path = (
                (self.repo_root / requested_path).resolve()
                if not requested_path.is_absolute()
                else requested_path.resolve()
            )
            if file_path != self.repo_root and self.repo_root not in file_path.parents:
                raise ValueError("Tool file_path must stay inside the repository root.")
            if file_path.suffix.lower() not in DEFAULT_EXTENSIONS:
                raise ValueError("Tools can only inspect or modify .py files.")
            if RepositorySearchTool._should_skip_path(file_path):
                raise ValueError("Tools cannot inspect or modify internal directories.")

            if name == "find_bug_lines":
                query = str(arguments["query"])
                context_lines = max(0, min(int(arguments.get("context_lines", 2)), 50))
                return find_bug_lines(file_path, query, context_lines)

            if not apply_patch:
                raise ValueError(
                    "Patch mode is disabled; replace_file cannot modify files."
                )
            old_snippet = str(arguments["old_snippet"])
            new_snippet = str(arguments["new_snippet"])
            if not old_snippet:
                raise ValueError("old_snippet must not be empty.")
            replaced = replace_file(file_path, old_snippet, new_snippet)
            return {
                "file_path": str(file_path.relative_to(self.repo_root)),
                "replaced": replaced,
                "old_snippet": old_snippet,
                "new_snippet": new_snippet,
            }

        raise ValueError(f"Unknown tool requested by LLM: {name}")


def search_repository(
    query: str, repo_root: str | Path = ".", limit: int = 10
) -> list[dict[str, Any]]:
    debug_log(
        f"search_repository wrapper called: query={query!r}, repo_root={repo_root!r}, limit={limit}"
    )
    result = RepositorySearchTool(repo_root).search(query, limit=limit)
    debug_log(f"search_repository wrapper finished with {len(result)} result(s).")
    return result


def find_bug_lines(
    file_path: str | Path, query: str, context_lines: int = 2
) -> list[dict[str, Any]]:
    debug_log(
        f"find_bug_lines wrapper called for file={file_path}, query={query!r}, context_lines={context_lines}"
    )
    result = BugLocatorTool(context_lines=context_lines).locate(file_path, query)
    debug_log(f"find_bug_lines wrapper found {len(result)} location(s).")
    return result


def replace_file(file_path: str | Path, old_snippet: str, new_snippet: str) -> bool:
    debug_log(f"replace_file wrapper called for {file_path}")
    result = FileReplacementTool().replace(file_path, old_snippet, new_snippet)
    debug_log(f"replace_file wrapper result={result}")
    return result


def main(argv: list[str] | None = None) -> None:
    debug_log("Program started.")
    parser = argparse.ArgumentParser(
        description="Find likely bug locations in a repository and optionally patch a targeted file.",
    )
    parser.add_argument(
        "description", nargs="?", help="Plain-English description of the bug"
    )
    parser.add_argument("--repo-root", default=".", help="Repository root to inspect")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply a simple targeted patch when a matching fix is detected",
    )
    args = parser.parse_args(argv)

    description = args.description
    if not description:
        if sys.stdin.isatty():
            description = input("Describe the bug in plain English: ").strip()
        else:
            description = sys.stdin.read().strip()

    if not description:
        debug_log("No bug description provided; showing help text.")
        parser.print_help()
        return

    debug_log(
        f"CLI arguments parsed: description={description!r}, repo_root={args.repo_root!r}, apply={args.apply}"
    )
    result = BugFixAgent(args.repo_root).run(description, apply_patch=args.apply)
    print(f"Status: {result['status']}")

    if result.get("files"):
        print("Candidate files:")
        for file_name in result["files"]:
            print(f"- {file_name}")

    if result.get("locations"):
        print("Relevant lines:")
        for location in result["locations"][:5]:
            print(f"  Line {location['line_number']}: {location['line']}")

    if result.get("patched"):
        print("Patch applied successfully.")

    llm_analysis = result.get("llm_analysis")
    if llm_analysis:
        print("\nLLM reasoning:")
        print(llm_analysis)

    debug_log("Program finished.")


if __name__ == "__main__":
    main()
