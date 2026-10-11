#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""String substitution engine for UI page objects.

=============================================================================
THE ONE RULE THAT MATTERS
=============================================================================
Serialized UI strings are stored as `7-bit varint(length) + UTF-8 bytes`.
You may change the length - the engine copes with that perfectly well - but
you MUST replace the *whole* `varint + text` pattern. Never do a bare
`bytes.replace(en, translation)`:

    # SAFE (mode="strict") -- the length prefix disambiguates
    b"\x0bCOMPUTERS" -> b"\x09\xe8\xae\xa1\xe7\xae\x97\xe6\x9c\xba"

    # LETHAL (mode="padded" / bare replace)
    "Environment" -> "环境" also rewrites:
        itemEnvironment        (control name)
        itemEnvironmentDLC     (control name)
        UI/ControlPanel/EnvironmentSheet   (asset URL)
    ... the control lookup then fails, ControlPanel.UnloadContent() throws a
    NullReferenceException, and the process dies with SIGSEGV ~15 s after
    launch. This cost four full rebuild cycles (T16, T17, T20) before the
    cause was isolated; see docs/07-pitfalls.md, pitfall #3.

An early version of this project concluded "UI strings must be replaced
in-place at equal byte length". That conclusion was WRONG. Length is free;
precision is not.

=============================================================================
"""
from __future__ import annotations

from stride_bundle import rvint, wr_vint


# --------------------------------------------------------------- TSV tables

def load_pairs(path: str) -> list[tuple[str, str]]:
    """Load a translation table from TSV:  <source>\\t<target>[\\t<comment>]

    Lines starting with '#' and blank lines are ignored. Entries are returned
    sorted by *source byte length, descending* so that longer phrases are
    replaced before their own substrings.
    """
    pairs = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                raise ValueError(f"{path}:{lineno}: expected <source>TAB<target>")
            src, dst = parts[0], parts[1]
            if not src or not dst:
                continue
            pairs.append((src, dst))
    return sorted(pairs, key=lambda kv: -len(kv[0].encode("utf-8")))


def codepoints_of(pairs, extra: str = "") -> list[int]:
    """Every codepoint that must exist in the font atlas for these strings.

    Do not filter this to "CJK only": punctuation is a real glyph too, and a
    missing glyph makes the font lookup return null, which is one of the ways
    you end up in the UnloadContent NRE chain.
    """
    cps: set[int] = set()
    for _src, dst in pairs:
        cps.update(ord(c) for c in dst)
    cps.update(ord(c) for c in extra)
    return sorted(cps)


# ------------------------------------------------------------- substitution

def apply_translations(group: bytes, pairs, mode: str = "strict",
                       phase2_min: int = 6, verbose: bool = True):
    """Return (new_group, hits).

    hits is a list of (source, target, count, mode).

    mode
    ----
    strict : varint+text exact match only.  **The only safe mode.**
    equal  : pad every target with spaces to the source byte length and do a
             bare text replace. Keeps length invariant but still corrupts
             control names - kept for historical reproduction, do not ship it.
    padded : strict first, then `equal` for whatever strict missed.
             Same hazard as `equal`.
    field  : varint-anchored field replacement. Matches a serialised field
             whose *stripped* content equals the source, and rewrites the
             field body in place (same length, so even the varint is
             untouched). Safe for the same reason `strict` is safe - the
             match starts at a length prefix - but it also catches labels
             that carry padding spaces, which plain strict misses.
    """
    if mode not in ("strict", "equal", "padded", "field", "strict-field"):
        raise ValueError(f"unknown mode {mode!r}")

    out = group
    hits: list[tuple[str, str, int, str]] = []
    skipped: list[tuple[str, str, int, int]] = []

    if mode == "equal":
        for src, dst in pairs:
            sb, db = src.encode("utf-8"), dst.encode("utf-8")
            if len(db) > len(sb):
                skipped.append((src, dst, len(sb), len(db)))
                continue
            padded = db + b" " * (len(sb) - len(db))
            n = out.count(sb)
            if n:
                out = out.replace(sb, padded)
                hits.append((src, dst, n, "equal"))
        _report(skipped, verbose)
        return out, hits

    # ---- phase 1: exact varint pattern -------------------------------------
    leftover = []
    for src, dst in pairs:
        sb, db = src.encode("utf-8"), dst.encode("utf-8")
        pattern = wr_vint(len(sb)) + sb
        replacement = wr_vint(len(db)) + db
        n = out.count(pattern)
        if n:
            out = out.replace(pattern, replacement)
            hits.append((src, dst, n, "varint"))
        else:
            leftover.append((src, dst))

    if mode in ("strict",):
        return out, hits

    if mode in ("field", "strict-field"):
        return _apply_field_mode(out, leftover, hits, verbose)

    # ---- phase 2 (unsafe): bare text replace for phase-1 misses ------------
    for src, dst in leftover:
        sb, db = src.encode("utf-8"), dst.encode("utf-8")
        if len(sb) < phase2_min or len(db) > len(sb):
            continue
        padded = db + b" " * (len(sb) - len(db))
        n = out.count(sb)
        if n:
            out = out.replace(sb, padded)
            hits.append((src, dst, n, "padded"))
    return out, hits


def scan_fields(group: bytes):
    """Map stripped field bodies -> list of (varint_pos, body_pos, length).

    Walks every byte offset, reads a varint, and keeps the candidate when the
    following `length` bytes are a plausible field. Cheap (one pass) and it
    gives the length-prefix anchor that makes field mode safe.
    """
    fields: dict[bytes, list[tuple[int, int, int]]] = {}
    n = len(group)
    p = 0
    while p < n - 1:
        try:
            length, q = rvint(group, p)
        except (IndexError, ValueError):
            # Only a truncated varint should end the walk - never swallow a
            # NameError/AttributeError here, or the scan silently returns
            # nothing and every field-mode substitution silently no-ops.
            break
        if 1 <= length <= 400 and q + length <= n:
            body = group[q:q + length]
            fields.setdefault(body.strip(b" "), []).append((p, q, length))
        p += 1
    return fields


def _apply_field_mode(group: bytes, pairs, hits, verbose: bool):
    """Rewrite serialised fields in place, preserving their exact length.

    Only fields whose stripped body equals the source are touched, so control
    names (`itemEnvironment` != `Environment`) and asset URLs are never hit.
    Because the length never changes, the varint prefix is not even rewritten.
    """
    out = bytearray(group)
    fields = scan_fields(bytes(out))
    used = set()
    for src, dst in pairs:
        key = src.strip(" ").encode("utf-8")
        db = dst.encode("utf-8")
        for varint_pos, body_pos, length in fields.get(key, ()):
            if (varint_pos, body_pos) in used:
                continue
            if len(db) > length:
                if verbose:
                    print(f"    field mode skipped (too long): {src[:40]!r} "
                          f"{length}B -> {len(db)}B")
                continue
            out[body_pos:body_pos + length] = db + b" " * (length - len(db))
            used.add((varint_pos, body_pos))
            hits.append((src, dst, 1, "field"))
    return bytes(out), hits


def _report(skipped, verbose: bool) -> None:
    if skipped and verbose:
        print("    skipped (target longer than source, equal mode):")
        for src, dst, a, b in skipped:
            print(f"      {src[:44]!r} {a}B -> {b}B")


def count_residual(group: bytes, pairs) -> list[tuple[str, int]]:
    """How many raw source strings are still present? (sanity check)

    A non-zero result is usually a *false alarm*: control names such as
    `tbFramerate` or `itemEnvironment` legitimately contain the English word.
    Only investigate when the string also appears as a standalone field.
    """
    left = []
    for src, _dst in pairs:
        n = group.count(src.encode("utf-8"))
        if n:
            left.append((src, n))
    return left
