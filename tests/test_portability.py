"""Keep published sources free of personal home paths and credential literals."""

import re


def test_sources_do_not_embed_machine_paths_or_credentials(repo_root):
    patterns = (
        re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
        re.compile(r"~/(?:repos|projects)/"),
        re.compile(r"/(?:opt/homebrew|usr/local/Cellar)/"),
        re.compile(r"\b(?:hf_|sk-(?:proj-|svcacct-)?)[A-Za-z0-9]{24,}\b"),
    )
    suffixes = {
        ".py",
        ".md",
        ".json",
        ".jsonl",
        ".toml",
        ".yaml",
        ".yml",
        ".example",
        ".txt",
        ".cpp",
        ".h",
        ".mm",
        ".metal",
        ".sh",
        ".ini",
    }
    files = [
        path for path in repo_root.iterdir() if path.is_file() and not path.name.startswith(".")
    ]
    for name in ("agents", "src", "scripts", "tests", "docs"):
        files.extend(path for path in (repo_root / name).rglob("*") if path.is_file())
    for path in files:
        if path.suffix not in suffixes:
            continue
        content = path.read_text(encoding="utf-8")
        assert not any(pattern.search(content) for pattern in patterns), str(
            path.relative_to(repo_root)
        )
