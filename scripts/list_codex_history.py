#!/usr/bin/env python3
"""按会话列出 Codex history.jsonl 中记录的用户输入。"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, TextIO


DEFAULT_HISTORY_PATH = Path("~/.codex/history.jsonl")


@dataclass(frozen=True)
class HistoryEntry:
    """history.jsonl 中一条有效的用户输入。"""

    session_id: str
    timestamp: float
    text: str


@dataclass(frozen=True)
class SessionHistory:
    """聚合后的单个对话。"""

    session_id: str
    name: str
    entries: list[HistoryEntry]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="读取 Codex history.jsonl，并按对话列出用户提出的问题。"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_HISTORY_PATH,
        help="history.jsonl 路径（默认：~/.codex/history.jsonl）",
    )
    parser.add_argument(
        "--format",
        choices=("text", "markdown", "json"),
        default="text",
        help="输出格式（默认：text）",
    )
    parser.add_argument(
        "--index",
        type=Path,
        help="session_index.jsonl 路径（默认与输入文件位于同一目录）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="将结果写入文件；不指定时输出到终端",
    )
    parser.add_argument(
        "--session",
        action="append",
        default=[],
        help="只显示指定 session_id；可重复使用",
    )
    parser.add_argument(
        "--name",
        action="append",
        default=[],
        help="按对话名称筛选，忽略大小写并支持部分匹配；可重复使用",
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        help="最多显示多少个对话",
    )
    parser.add_argument(
        "--oldest-first",
        action="store_true",
        help="按最早对话优先排列（默认按最近对话优先）",
    )
    return parser.parse_args()


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须是大于 0 的整数")
    return number


def load_history(path: Path) -> tuple[list[HistoryEntry], list[str]]:
    entries: list[HistoryEntry] = []
    warnings: list[str] = []

    with path.open("r", encoding="utf-8") as history_file:
        for line_number, raw_line in enumerate(history_file, start=1):
            line = raw_line.strip()
            if not line:
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                warnings.append(f"第 {line_number} 行不是有效 JSON：{exc.msg}")
                continue

            if not isinstance(item, dict):
                warnings.append(f"第 {line_number} 行不是 JSON 对象，已跳过")
                continue

            session_id = item.get("session_id")
            timestamp = item.get("ts")
            text = item.get("text")
            if not isinstance(session_id, str) or not session_id:
                warnings.append(f"第 {line_number} 行缺少有效的 session_id，已跳过")
                continue
            if not isinstance(timestamp, (int, float)):
                warnings.append(f"第 {line_number} 行缺少有效的 ts，已跳过")
                continue
            if not isinstance(text, str):
                warnings.append(f"第 {line_number} 行缺少有效的 text，已跳过")
                continue

            timestamp_value = float(timestamp)
            try:
                timestamp_is_valid = math.isfinite(timestamp_value)
                if timestamp_is_valid:
                    datetime.fromtimestamp(timestamp_value)
            except (OverflowError, OSError, ValueError):
                timestamp_is_valid = False
            if not timestamp_is_valid:
                warnings.append(f"第 {line_number} 行的 ts 无法转换为时间，已跳过")
                continue

            entries.append(HistoryEntry(session_id, timestamp_value, text))

    return entries, warnings


def load_session_names(path: Path) -> tuple[dict[str, str], list[str]]:
    """读取会话名称；同一会话有多次改名时以文件中的最后一条为准。"""
    names: dict[str, str] = {}
    warnings: list[str] = []

    try:
        index_file = path.open("r", encoding="utf-8")
    except FileNotFoundError:
        warnings.append(f"未找到名称索引 {path}，将使用每个对话的首条输入作为名称")
        return names, warnings
    except OSError as exc:
        warnings.append(f"无法读取名称索引 {path}：{exc}；将使用首条输入作为名称")
        return names, warnings

    with index_file:
        for line_number, raw_line in enumerate(index_file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                warnings.append(
                    f"名称索引第 {line_number} 行不是有效 JSON：{exc.msg}"
                )
                continue

            if not isinstance(item, dict):
                warnings.append(f"名称索引第 {line_number} 行不是 JSON 对象，已跳过")
                continue
            session_id = item.get("id")
            thread_name = item.get("thread_name")
            if not isinstance(session_id, str) or not session_id:
                warnings.append(
                    f"名称索引第 {line_number} 行缺少有效的 id，已跳过"
                )
                continue
            if not isinstance(thread_name, str) or not thread_name.strip():
                warnings.append(
                    f"名称索引第 {line_number} 行缺少有效的 thread_name，已跳过"
                )
                continue
            names[session_id] = thread_name.strip()

    return names, warnings


def group_sessions(
    entries: Iterable[HistoryEntry],
    session_names: dict[str, str],
    selected_session_ids: set[str],
    selected_names: list[str],
    oldest_first: bool,
    limit: int | None,
) -> list[SessionHistory]:
    grouped: dict[str, list[HistoryEntry]] = defaultdict(list)
    for entry in entries:
        if selected_session_ids and entry.session_id not in selected_session_ids:
            continue
        grouped[entry.session_id].append(entry)

    normalized_names = [name.casefold() for name in selected_names]
    sessions: list[SessionHistory] = []
    for session_id, session_entries in grouped.items():
        session_entries.sort(key=lambda entry: entry.timestamp)
        name = session_names.get(session_id, one_line(session_entries[0].text))
        if normalized_names and not any(
            selected_name in name.casefold() for selected_name in normalized_names
        ):
            continue
        sessions.append(SessionHistory(session_id, name, session_entries))

    sessions.sort(
        key=lambda session: session.entries[-1].timestamp,
        reverse=not oldest_first,
    )
    return sessions[:limit]


def format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def one_line(text: str, max_length: int = 80) -> str:
    compact = " ".join(text.split()) or "（空输入）"
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 1] + "…"


def indent_text(text: str, prefix: str) -> str:
    lines = text.splitlines() or [""]
    return "\n".join(prefix + line for line in lines)


def render_text(sessions: list[SessionHistory], output: TextIO) -> None:
    question_count = sum(len(session.entries) for session in sessions)
    print(f"共找到 {len(sessions)} 个对话，{question_count} 条用户输入。", file=output)

    for session_number, session in enumerate(sessions, start=1):
        entries = session.entries
        print(file=output)
        print(f"[{session_number}] {session.name}", file=output)
        print(f"    对话名称: {session.name}", file=output)
        print(f"    session_id: {session.session_id}", file=output)
        print(
            f"    时间: {format_time(entries[0].timestamp)} ～ "
            f"{format_time(entries[-1].timestamp)}",
            file=output,
        )
        print(f"    用户输入数: {len(entries)}", file=output)
        for question_number, entry in enumerate(entries, start=1):
            print(
                f"    {question_number}. [{format_time(entry.timestamp)}]",
                file=output,
            )
            print(indent_text(entry.text, "       "), file=output)


def render_markdown(
    sessions: list[SessionHistory], output: TextIO
) -> None:
    question_count = sum(len(session.entries) for session in sessions)
    print("# Codex 对话历史", file=output)
    print(file=output)
    print(f"共找到 {len(sessions)} 个对话，{question_count} 条用户输入。", file=output)

    for session_number, session in enumerate(sessions, start=1):
        entries = session.entries
        print(file=output)
        print(f"## {session_number}. {session.name}", file=output)
        print(file=output)
        print(f"- 对话名称：{session.name}", file=output)
        print(f"- Session ID：`{session.session_id}`", file=output)
        print(
            f"- 时间：{format_time(entries[0].timestamp)} ～ "
            f"{format_time(entries[-1].timestamp)}",
            file=output,
        )
        print(f"- 用户输入数：{len(entries)}", file=output)
        for question_number, entry in enumerate(entries, start=1):
            print(file=output)
            print(f"### 输入 {question_number} · {format_time(entry.timestamp)}", file=output)
            print(file=output)
            print(indent_text(entry.text, "> "), file=output)


def render_json(sessions: list[SessionHistory], output: TextIO) -> None:
    payload = []
    for session in sessions:
        entries = session.entries
        payload.append(
            {
                "session_id": session.session_id,
                "conversation_name": session.name,
                "title": one_line(entries[0].text),
                "started_at": format_time(entries[0].timestamp),
                "updated_at": format_time(entries[-1].timestamp),
                "user_input_count": len(entries),
                "user_inputs": [
                    {
                        "timestamp": entry.timestamp,
                        "time": format_time(entry.timestamp),
                        "text": entry.text,
                    }
                    for entry in entries
                ],
            }
        )
    json.dump(payload, output, ensure_ascii=False, indent=2)
    print(file=output)


def write_result(
    sessions: list[SessionHistory],
    output_format: str,
    output_path: Path | None,
) -> None:
    renderer = {
        "text": render_text,
        "markdown": render_markdown,
        "json": render_json,
    }[output_format]

    if output_path is None:
        renderer(sessions, sys.stdout)
        return

    output_path = output_path.expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        renderer(sessions, output_file)
    print(f"结果已写入：{output_path}", file=sys.stderr)


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser()
    index_path = (
        args.index.expanduser()
        if args.index is not None
        else input_path.parent / "session_index.jsonl"
    )

    try:
        entries, history_warnings = load_history(input_path)
    except OSError as exc:
        print(f"无法读取历史文件 {input_path}：{exc}", file=sys.stderr)
        return 1

    session_names, name_warnings = load_session_names(index_path)
    for warning in history_warnings + name_warnings:
        print(f"警告：{warning}", file=sys.stderr)

    sessions = group_sessions(
        entries,
        session_names=session_names,
        selected_session_ids=set(args.session),
        selected_names=args.name,
        oldest_first=args.oldest_first,
        limit=args.limit,
    )
    write_result(sessions, args.format, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
