#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║          VS Code .code-profile  ↔  Pretty JSON Converter         ║
╚══════════════════════════════════════════════════════════════════╝

Usage:
  # .code-profile  →  pretty JSON
  python code_profile_converter.py decode  input.code-profile  output.json

  # pretty JSON  →  .code-profile
  python code_profile_converter.py encode  input.json  output.code-profile

What this does:
  A .code-profile is a deeply nested, multiply-stringified JSON file.
  Fields like "settings", "keybindings", "globalState", and "extensions"
  are stored as JSON-encoded strings (some with JSONC comments), which are
  themselves stored inside another JSON-encoded string wrapper. This script
  unwraps all of that into clean, readable nested objects — and can
  re-wrap them back into the original format faithfully.
"""

import json
import re
import sys
import argparse
from pathlib import Path


# ─────────────────────────────────────────────────────────────────
# JSONC comment stripping
# ─────────────────────────────────────────────────────────────────

def strip_jsonc_comments(text: str) -> str:
    """
    Remove single-line (// ...) and block (/* ... */) comments from a
    JSONC string, while leaving strings that contain '//' intact.
    Also removes trailing commas before } or ] (VS Code settings allow them).
    """
    result = []
    i = 0
    in_string = False
    escape_next = False

    while i < len(text):
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == '\\' and in_string:
            result.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"' and not escape_next:
            in_string = not in_string
            result.append(ch)
            i += 1
            continue

        if in_string:
            result.append(ch)
            i += 1
            continue

        # Outside a string — check for comments
        if ch == '/' and i + 1 < len(text):
            next_ch = text[i + 1]

            # Single-line comment: // ...
            if next_ch == '/':
                while i < len(text) and text[i] != '\n':
                    i += 1
                continue

            # Block comment: /* ... */
            if next_ch == '*':
                i += 2
                while i < len(text) - 1:
                    if text[i] == '*' and text[i + 1] == '/':
                        i += 2
                        break
                    i += 1
                continue

        result.append(ch)
        i += 1

    stripped = ''.join(result)

    # Remove trailing commas before closing braces/brackets
    stripped = re.sub(r',\s*([}\]])', r'\1', stripped)

    return stripped


# ─────────────────────────────────────────────────────────────────
# Deep-parse helpers
# ─────────────────────────────────────────────────────────────────

def try_parse_json_string(value: str, strip_comments: bool = False) -> object:
    """
    Attempt to parse a string as JSON.
    Returns the parsed object on success, or the original string on failure.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    if text[0] not in ('{', '[', '"', 't', 'f', 'n') and not text[0].isdigit() and text[0] != '-':
        return value
    try:
        if strip_comments:
            text = strip_jsonc_comments(text)
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return value


def decode_settings_field(raw: str) -> object:
    """
    The 'settings' field: outer JSON string → { "settings": "<JSONC string>" }
    Strip comments from the inner JSONC, then parse it.
    Returns a clean dict ready for the pretty output.
    """
    outer = json.loads(raw)                         # { "settings": "..." }
    inner_text = outer.get("settings", "")
    parsed = try_parse_json_string(inner_text, strip_comments=True)
    return parsed


def decode_keybindings_field(raw: str) -> object:
    """
    The 'keybindings' field: outer JSON string → { "keybindings": "[...]", "platform": N }
    Inner value is a JSON array string; VS Code allows trailing commas in it.
    Returns { "keybindings": [...], "platform": N }.
    """
    outer = json.loads(raw)                         # { "keybindings": "...", "platform": N }
    kb_text = outer.get("keybindings", "")
    # VS Code keybindings.json also permits trailing commas (JSONC superset)
    parsed_kb = try_parse_json_string(kb_text, strip_comments=True)
    if isinstance(parsed_kb, str):
        # strip_comments wasn't enough — try trailing-comma-only strip
        cleaned = re.sub(r',\s*([}\]])', r'\1', kb_text)
        try:
            parsed_kb = json.loads(cleaned)
        except json.JSONDecodeError:
            parsed_kb = kb_text          # give up; keep as string
    return {
        "keybindings": parsed_kb,
        "platform": outer.get("platform"),
    }


def decode_global_state_field(raw: str) -> object:
    """
    The 'globalState' field: outer JSON string → { "storage": { key: "<JSON string>", ... } }
    Many storage values are themselves JSON-encoded strings — parse those too.
    """
    outer = json.loads(raw)                         # { "storage": { ... } }
    storage = outer.get("storage", {})
    decoded_storage = {}
    for key, val in storage.items():
        decoded_storage[key] = try_parse_json_string(val)
    return {"storage": decoded_storage}


def decode_extensions_field(raw: str) -> object:
    """
    The 'extensions' field is a single JSON-encoded string containing an array.
    """
    return json.loads(raw)                          # list of extension dicts


# ─────────────────────────────────────────────────────────────────
# Re-encode helpers  (pretty JSON → .code-profile)
# ─────────────────────────────────────────────────────────────────

def encode_settings_field(value: object) -> str:
    """Wrap parsed settings back into { "settings": "<JSON string>" } then stringify."""
    inner_json = json.dumps(value, indent=4, ensure_ascii=False)
    outer = {"settings": inner_json}
    return json.dumps(outer, ensure_ascii=False)


def encode_keybindings_field(value: object) -> str:
    """Wrap parsed keybindings back into { "keybindings": "[...]", "platform": N }."""
    kb_list = value.get("keybindings", [])
    platform = value.get("platform", 2)
    kb_json = json.dumps(kb_list, indent=4, ensure_ascii=False)
    outer = {"keybindings": kb_json, "platform": platform}
    return json.dumps(outer, ensure_ascii=False)


def encode_global_state_field(value: object) -> str:
    """Re-stringify storage values that were originally JSON-encoded strings."""
    storage_in = value.get("storage", {})
    storage_out = {}
    for key, val in storage_in.items():
        if isinstance(val, (dict, list)):
            storage_out[key] = json.dumps(val, ensure_ascii=False)
        else:
            storage_out[key] = val
    outer = {"storage": storage_out}
    return json.dumps(outer, ensure_ascii=False)


def encode_extensions_field(value: object) -> str:
    """Re-stringify the extensions array."""
    return json.dumps(value, ensure_ascii=False)


# ─────────────────────────────────────────────────────────────────
# Main decode:  .code-profile  →  pretty JSON
# ─────────────────────────────────────────────────────────────────

def decode(input_path: Path, output_path: Path) -> None:
    print(f"  Reading  : {input_path}")
    raw = input_path.read_text(encoding="utf-8")
    data = json.loads(raw)

    pretty = {}

    # ── name ──────────────────────────────────────────────────────
    pretty["name"] = data.get("name", "")

    # ── settings ──────────────────────────────────────────────────
    if "settings" in data and data["settings"]:
        try:
            pretty["settings"] = decode_settings_field(data["settings"])
        except Exception as exc:
            print(f"  ⚠  Could not fully decode 'settings': {exc}")
            pretty["settings"] = data["settings"]

    # ── keybindings ───────────────────────────────────────────────
    if "keybindings" in data and data["keybindings"]:
        try:
            pretty["keybindings"] = decode_keybindings_field(data["keybindings"])
        except Exception as exc:
            print(f"  ⚠  Could not fully decode 'keybindings': {exc}")
            pretty["keybindings"] = data["keybindings"]

    # ── extensions ────────────────────────────────────────────────
    if "extensions" in data and data["extensions"]:
        try:
            pretty["extensions"] = decode_extensions_field(data["extensions"])
        except Exception as exc:
            print(f"  ⚠  Could not fully decode 'extensions': {exc}")
            pretty["extensions"] = data["extensions"]

    # ── globalState ───────────────────────────────────────────────
    if "globalState" in data and data["globalState"]:
        try:
            pretty["globalState"] = decode_global_state_field(data["globalState"])
        except Exception as exc:
            print(f"  ⚠  Could not fully decode 'globalState': {exc}")
            pretty["globalState"] = data["globalState"]

    # ── any other unknown top-level keys ──────────────────────────
    known_keys = {"name", "settings", "keybindings", "extensions", "globalState"}
    for key in data:
        if key not in known_keys:
            pretty[key] = data[key]

    json_output = json.dumps(pretty, indent=4, ensure_ascii=False)
    output_path.write_text(json_output, encoding="utf-8")

    size_in  = len(raw)
    size_out = len(json_output)
    print(f"  Writing  : {output_path}")
    print(f"  Input    : {size_in:,} bytes  →  Output: {size_out:,} bytes")
    print(f"  ✔  Decoded successfully.\n")


# ─────────────────────────────────────────────────────────────────
# Main encode:  pretty JSON  →  .code-profile
# ─────────────────────────────────────────────────────────────────

def encode(input_path: Path, output_path: Path) -> None:
    print(f"  Reading  : {input_path}")
    raw = input_path.read_text(encoding="utf-8")
    pretty = json.loads(raw)

    profile = {}

    # ── name ──────────────────────────────────────────────────────
    profile["name"] = pretty.get("name", "")

    # ── settings ──────────────────────────────────────────────────
    if "settings" in pretty and pretty["settings"]:
        try:
            profile["settings"] = encode_settings_field(pretty["settings"])
        except Exception as exc:
            print(f"  ⚠  Could not re-encode 'settings': {exc}")
            profile["settings"] = pretty["settings"]

    # ── keybindings ───────────────────────────────────────────────
    if "keybindings" in pretty and pretty["keybindings"]:
        try:
            profile["keybindings"] = encode_keybindings_field(pretty["keybindings"])
        except Exception as exc:
            print(f"  ⚠  Could not re-encode 'keybindings': {exc}")
            profile["keybindings"] = pretty["keybindings"]

    # ── extensions ────────────────────────────────────────────────
    if "extensions" in pretty and pretty["extensions"]:
        try:
            profile["extensions"] = encode_extensions_field(pretty["extensions"])
        except Exception as exc:
            print(f"  ⚠  Could not re-encode 'extensions': {exc}")
            profile["extensions"] = pretty["extensions"]

    # ── globalState ───────────────────────────────────────────────
    if "globalState" in pretty and pretty["globalState"]:
        try:
            profile["globalState"] = encode_global_state_field(pretty["globalState"])
        except Exception as exc:
            print(f"  ⚠  Could not re-encode 'globalState': {exc}")
            profile["globalState"] = pretty["globalState"]

    # ── any other top-level keys ──────────────────────────────────
    known_keys = {"name", "settings", "keybindings", "extensions", "globalState"}
    for key in pretty:
        if key not in known_keys:
            profile[key] = pretty[key]

    profile_output = json.dumps(profile, ensure_ascii=False)
    output_path.write_text(profile_output, encoding="utf-8")

    size_in  = len(raw)
    size_out = len(profile_output)
    print(f"  Writing  : {output_path}")
    print(f"  Input    : {size_in:,} bytes  →  Output: {size_out:,} bytes")
    print(f"  ✔  Encoded successfully.\n")


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║       VS Code .code-profile  ↔  Pretty JSON Converter            ║
╚══════════════════════════════════════════════════════════════════╝"""

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── decode sub-command ────────────────────────────────────────
    dec = subparsers.add_parser(
        "decode",
        help=".code-profile  →  pretty JSON",
        description="Unwrap a .code-profile into a human-readable JSON file.",
    )
    dec.add_argument("input",  type=Path, help="Path to the .code-profile file")
    dec.add_argument("output", type=Path, nargs="?",
                     help="Output .json path (default: <input>.json)")

    # ── encode sub-command ────────────────────────────────────────
    enc = subparsers.add_parser(
        "encode",
        help="pretty JSON  →  .code-profile",
        description="Re-wrap a pretty JSON file back into a .code-profile.",
    )
    enc.add_argument("input",  type=Path, help="Path to the pretty .json file")
    enc.add_argument("output", type=Path, nargs="?",
                     help="Output .code-profile path (default: <input>.code-profile)")

    args = parser.parse_args()
    print(BANNER)

    if args.command == "decode":
        in_path  = args.input
        out_path = args.output or in_path.with_suffix(".json")
        if not in_path.exists():
            print(f"\n  ✖  File not found: {in_path}\n", file=sys.stderr)
            sys.exit(1)
        print()
        decode(in_path, out_path)

    elif args.command == "encode":
        in_path  = args.input
        out_path = args.output or in_path.with_suffix(".code-profile")
        if not in_path.exists():
            print(f"\n  ✖  File not found: {in_path}\n", file=sys.stderr)
            sys.exit(1)
        print()
        encode(in_path, out_path)


if __name__ == "__main__":
    main()
