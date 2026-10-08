"""Compare a checked-out FTS5 candidate with a pinned main history reader.

Run from a nanobot checkout. All fixtures and caches are created under --work-dir.
The script neither calls a model API nor modifies existing session data.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--base", required=True, help="Git commit containing the main history reader")
parser.add_argument("--work-dir", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--repeats", type=int, default=3)
args = parser.parse_args()
repo = Path.cwd()
sys.path.insert(0, str(repo))

import nanobot.session.manager as manager_module
from nanobot.session.history import SessionHistoryReader
from nanobot.session.manager import SessionManager

args.work_dir.mkdir(parents=True, exist_ok=True)
fixtures = Path(tempfile.mkdtemp(prefix="canonical-fts5-", dir=args.work_dir))
manager_module.get_legacy_sessions_dir = lambda: fixtures / "absent-legacy"
namespace = {"__name__": "pinned_main_history"}
exec(compile(subprocess.check_output(
    ["git", "show", f"{args.base}:nanobot/session/history.py"],
    cwd=repo, text=True, encoding="utf-8",
), "pinned_main_history.py", "exec"), namespace)
MainReader = namespace["SessionHistoryReader"]


def measure(action, repeats=1):
    samples = []
    result = None
    for _ in range(repeats):
        start = time.perf_counter()
        result = action()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples), result


def scenario(label, sessions, messages):
    root = fixtures / label
    manager = SessionManager(root / "workspace", sessions_root=root / "runtime")
    for index in range(sessions):
        session = manager.get_or_create(f"sdk:session-{index:04d}")
        session.metadata.update({"title": f"Project {index}", "title_user_edited": True})
        for message in range(messages):
            text = (
                "zebra-target launch decision" if index == 0 and message == 0
                else f"Ordinary project update {message}: " + "x" * 90
            )
            session.add_message("user" if message % 2 == 0 else "assistant", text)
        session.updated_at = datetime(2024 if index == 0 else 2025, 1, 1)
        manager.save(session)

    calls = []
    original = manager.read_session_file

    def counted_read(key):
        calls.append(key)
        return original(key)

    manager.read_session_file = counted_read
    main = MainReader(manager)
    main_ms, expected = measure(lambda: main.search("zebra-target", 5), args.repeats)
    main_reads = len(calls) // args.repeats
    calls.clear()
    indexed = SessionHistoryReader(manager)
    first_ms, actual = measure(lambda: indexed.search("zebra-target", 5))
    assert actual == expected
    first_reads = len(calls)
    calls.clear()
    warm_ms, actual = measure(lambda: indexed.search("zebra-target", 5), args.repeats)
    assert actual == expected
    warm_reads = len(calls) // args.repeats
    fresh_ms, actual = measure(
        lambda: SessionHistoryReader(manager).search("zebra-target", 5), args.repeats,
    )
    assert actual == expected
    main_miss_ms, expected_miss = measure(lambda: main.search("absent-marker", 5), args.repeats)
    warm_miss_ms, actual_miss = measure(lambda: indexed.search("absent-marker", 5), args.repeats)
    assert actual_miss == expected_miss == []

    def batched_main():
        with manager.locked_session_files():
            return main.search("zebra-target", 5)

    batched_ms, actual = measure(batched_main, args.repeats)
    assert actual == expected
    return {
        "label": label, "sessions": sessions, "messages_per_session": messages,
        "canonical_bytes": sum(p.stat().st_size for p in manager.sessions_dir.glob("*.jsonl")),
        "cache_bytes": (manager.sessions_dir / ".session_search.sqlite3").stat().st_size,
        "main_match_ms": round(main_ms, 2), "first_index_match_ms": round(first_ms, 2),
        "warm_index_match_ms": round(warm_ms, 2), "new_reader_warm_match_ms": round(fresh_ms, 2),
        "main_no_match_ms": round(main_miss_ms, 2), "warm_index_no_match_ms": round(warm_miss_ms, 2),
        "main_batched_lock_only_ms": round(batched_ms, 2),
        "warm_match_speedup": round(main_ms / warm_ms, 2),
        "fts_speedup_over_batched_main": round(batched_ms / warm_ms, 2),
        "main_body_reads": main_reads, "first_index_body_reads": first_reads,
        "warm_index_body_reads": warm_reads, "outputs_equal": True,
    }


results = []
for workload in [("small", 200, 20), ("large", 300, 500)]:
    result = scenario(*workload)
    results.append(result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
evidence = {
    "base_sha": subprocess.check_output(["git", "rev-parse", args.base], text=True).strip(),
    "candidate_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "platform": platform.platform(), "python": sys.version, "sqlite": sqlite3.sqlite_version,
    "repeats": args.repeats,
    "method": "Synthetic canonical JSONL; three-run medians, warm OS cache; first index separately timed",
    "results": results,
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
