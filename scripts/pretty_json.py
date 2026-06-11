import argparse
import json
import sys
from pathlib import Path


def format_json_text(text, indent=2, compact=False):
    payload = expand_embedded_json(json.loads(text))
    if compact:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    return json.dumps(payload, ensure_ascii=False, indent=indent) + "\n"


def format_jsonl_text(text, indent=2, compact=False, readable=False):
    lines = [line for line in text.splitlines() if line.strip()]
    formatted = []
    for index, line in enumerate(lines, 1):
        payload = json.loads(line)
        if readable:
            payload = expand_embedded_json(payload)
        if readable:
            formatted.append(f"===== record {index} =====")
            formatted.append(json.dumps(payload, ensure_ascii=False, indent=indent))
        elif compact:
            formatted.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        else:
            formatted.append(json.dumps(payload, ensure_ascii=False, indent=indent))
    return "\n".join(formatted) + ("\n" if formatted else "")


def expand_embedded_json(value):
    if isinstance(value, dict):
        return {key: expand_embedded_json(parse_json_string(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_embedded_json(item) for item in value]
    return value


def parse_json_string(value):
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def infer_kind(path, explicit_kind=None):
    if explicit_kind:
        return explicit_kind
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "jsonl"
    return "json"


def format_path(path, kind=None, indent=2, compact=False, readable=False):
    resolved_kind = infer_kind(path, kind)
    text = path.read_text(encoding="utf-8")
    if resolved_kind == "jsonl":
        return format_jsonl_text(text, indent=indent, compact=compact, readable=readable)
    return format_json_text(text, indent=indent, compact=compact)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Pretty-print .json and .jsonl files.")
    parser.add_argument("path", type=Path)
    parser.add_argument("--kind", choices=["json", "jsonl"])
    parser.add_argument("--indent", type=int, default=2)
    parser.add_argument("--compact", action="store_true", help="Write compact JSON.")
    parser.add_argument("--jsonl-readable", action="store_true", help="Expand jsonl records with separators.")
    parser.add_argument("--in-place", action="store_true", help="Overwrite the input file.")
    parser.add_argument("--output", type=Path, help="Write formatted content to another file.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    content = format_path(
        args.path,
        kind=args.kind,
        indent=args.indent,
        compact=args.compact,
        readable=args.jsonl_readable,
    )
    if args.in_place:
        args.path.write_text(content, encoding="utf-8")
        print(f"Wrote {args.path}")
    elif args.output:
        args.output.write_text(content, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(content)


if __name__ == "__main__":
    main()
