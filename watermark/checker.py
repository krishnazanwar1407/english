"""Detect invisible / suspicious Unicode characters in a JSON file.

Edit JSON_PATH below, then run:  python invisible_json_checker.py
(Optionally pass a path as argv[1] to override.)

Two passes:
  1. Raw-text pass   -> exact line/column of characters literally in the file.
  2. Parsed pass     -> JSON path of keys/values whose *decoded* string contains
                        invisible chars (catches "\\u200b" escapes, which the
                        raw pass cannot see as invisible).
Exit code: 0 = clean, 1 = findings, 2 = file/encoding/JSON error.
"""
from __future__ import annotations

import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# ----------------------------------------------------------------------------
JSON_PATH = Path(r"C:\path\to\your\file.json")   # <-- set your file here
# ----------------------------------------------------------------------------

# Characters allowed as ordinary JSON whitespace outside/inside text.
ALLOWED_CONTROLS = {"\n", "\r", "\t"}

# Explicit extras that general categories miss or that deserve a clear label.
EXTRA_INVISIBLE = {
    0x00AD: "SOFT HYPHEN",
    0x034F: "COMBINING GRAPHEME JOINER",
    0x061C: "ARABIC LETTER MARK",
    0x115F: "HANGUL CHOSEONG FILLER",
    0x1160: "HANGUL JUNGSEONG FILLER",
    0x17B4: "KHMER VOWEL INHERENT AQ",
    0x17B5: "KHMER VOWEL INHERENT AA",
    0x180E: "MONGOLIAN VOWEL SEPARATOR",
    0x3164: "HANGUL FILLER",
    0xFFA0: "HALFWIDTH HANGUL FILLER",
    0x2800: "BRAILLE PATTERN BLANK",
    0xFEFF: "ZERO WIDTH NO-BREAK SPACE / BOM",
}


@dataclass
class Finding:
    where: str
    char: str
    reason: str

    def __str__(self) -> str:
        cp = ord(self.char)
        name = unicodedata.name(self.char, EXTRA_INVISIBLE.get(cp, "<unnamed>"))
        return f"{self.where}: U+{cp:04X} {name} [{self.reason}]"


def classify(ch: str) -> str | None:
    """Return a reason string if ch is invisible/suspicious, else None."""
    cp = ord(ch)
    if ch in ALLOWED_CONTROLS:
        return None
    if cp in EXTRA_INVISIBLE:
        return "known invisible"
    cat = unicodedata.category(ch)
    if cat == "Cf":
        return "format char (zero-width/bidi/etc.)"
    if cat == "Cc":
        return "control char"
    if cat in ("Zl", "Zp"):
        return "line/paragraph separator"
    if cat == "Zs" and ch != " ":
        return "non-ASCII space (NBSP, thin space, ...)"
    if cat == "Co":
        return "private use"
    if cat == "Cs":
        return "lone surrogate"
    if cat == "Cn":
        return "unassigned/noncharacter"
    if 0xFE00 <= cp <= 0xFE0F or 0xE0100 <= cp <= 0xE01EF:
        return "variation selector"
    if 0xE0000 <= cp <= 0xE007F:
        return "tag character"
    return None


def decode_bytes(raw: bytes) -> tuple[str, str]:
    """Strict decode with BOM sniffing. Returns (text, encoding_label)."""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8"), "utf-8 (with BOM - discouraged for JSON)"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16"), "utf-16 (BOM)"
    if raw.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return raw.decode("utf-32"), "utf-32 (BOM)"
    # RFC 8259: JSON must be UTF-8. Strict => invalid bytes raise, never replaced.
    return raw.decode("utf-8", errors="strict"), "utf-8"


def scan_raw(text: str) -> list[Finding]:
    out: list[Finding] = []
    line, col = 1, 0
    for ch in text:
        col += 1
        if ch == "\n":
            line, col = line + 1, 0
            continue
        reason = classify(ch)
        if reason:
            out.append(Finding(f"line {line}, col {col}", ch, reason))
    return out


def scan_parsed(node, path: str = "$") -> list[Finding]:
    out: list[Finding] = []

    def check(s: str, where: str) -> None:
        for i, ch in enumerate(s):
            reason = classify(ch)
            if reason:
                out.append(Finding(f"{where} (index {i})", ch, reason))

    if isinstance(node, dict):
        for k, v in node.items():
            check(k, f"{path} key {k!r}")
            out.extend(scan_parsed(v, f"{path}.{k}"))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(scan_parsed(v, f"{path}[{i}]"))
    elif isinstance(node, str):
        check(node, path)
    return out


def check_file(path: Path) -> int:
    try:
        raw = path.read_bytes()
        text, enc = decode_bytes(raw)
        data = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as e:
        print(f"ERROR reading {path}: {type(e).__name__}: {e}")
        return 2

    print(f"File: {path}\nEncoding: {enc}\nBytes: {len(raw)}")
    raw_hits = scan_raw(text)
    parsed_hits = scan_parsed(data)

    # Parsed hits not already visible in the raw text = came from \uXXXX escapes.
    escaped_only = len(parsed_hits) > len([h for h in raw_hits])

    if not raw_hits and not parsed_hits and "BOM" not in enc:
        print("OK: no invisible characters found.")
        return 0

    if "BOM" in enc:
        print("WARNING: file has a BOM; write JSON as UTF-8 *without* BOM.")
    if raw_hits:
        print(f"\nRaw-text findings ({len(raw_hits)}):")
        for f in raw_hits:
            print("  ", f)
    if parsed_hits:
        print(f"\nDecoded-value findings by JSON path ({len(parsed_hits)}):")
        for f in parsed_hits:
            print("  ", f)
    if escaped_only:
        print("\nNote: some hits come from \\uXXXX escapes (invisible once decoded).")
    return 1


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else JSON_PATH
    sys.exit(check_file(target))
