#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump candidate translatable strings out of the UI page objects.

    python tools/dump_strings.py [--config config.json] [--out work/strings.tsv]
                                 [--pages all] [--min-len 3]

This is the starting point for a NEW language: it produces a TSV whose left
column is the English source. Translate the right column, save it under
`locales/<your-lang>/strings.tsv`, point `language.strings` at it, and run
`tools/build.py`.

HEURISTIC - read this before trusting the output
------------------------------------------------
UI strings are serialised as `varint(length) + UTF-8`. We scan the
decompressed object group for byte positions where a varint is followed by
exactly that many printable ASCII bytes, and require that the byte before and
after the candidate is not alphanumeric (a genuine field has a precise
length). That is still a heuristic: expect 10-30% noise (fragments of control
names, URLs, shader-ish tokens). Sort by the `hits` column and eyeball it -
real UI text is what a human would recognise as a label.

Anything ending in a control-name prefix (btn/grid/tb/pn/chk/ed/lb...) or
looking like an asset URL is best deleted from the table, not translated:
renaming a control breaks resource lookup (docs/07-pitfalls.md #3).
"""
from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vdl_config import Config
from stride_bundle import BundleIndex, decompress_range, rvint

PRINTABLE = re.compile(rb"^[ -~]+$")
LIKELY_TEXT = re.compile(r"^[A-Za-z][A-Za-z0-9 .,'&:/()\-+%$°]*$")
CONTROL_PREFIX = re.compile(
    r"^(btn|grid|tb|pn|chk|ed|lb|img|pnl|cmb|lst|spn|chk|tab|item|row|col|"
    r"scr|vw|hdr|ftr|nav|msg|dlg|ctl|opt|grp)[A-Z]", re.I)


def scan(group: bytes, min_len: int = 3, max_len: int = 160):
    """Yield (string, occurrences) candidates found in one object group."""
    found: dict[str, int] = {}
    n = len(group)
    p = 0
    while p < n - 1:
        try:
            length, q = rvint(group, p)
        except Exception:
            break
        if length < min_len or length > max_len or q + length > n:
            p += 1
            continue
        blob = group[q:q + length]
        if not PRINTABLE.match(blob):
            p += 1
            continue
        # boundary check: a real field ends exactly where its length says
        if p > 0 and chr(group[p - 1]).isalnum():
            p += 1
            continue
        if q + length < n and chr(group[q + length]).isalnum():
            p += 1
            continue
        try:
            text = blob.decode("ascii")
        except Exception:
            p += 1
            continue
        if LIKELY_TEXT.match(text) and not CONTROL_PREFIX.match(text):
            found[text] = found.get(text, 0) + 1
        p += 1
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--out", default=None, help="output TSV (default work/strings.tsv)")
    ap.add_argument("--pages", default="all")
    ap.add_argument("--min-len", type=int, default=3)
    ap.add_argument("--set", action="append", default=[], metavar="a.b=value")
    args = ap.parse_args()

    cfg = Config.load(args.config, args.set)
    src_data = cfg.path("bundles.data")
    if not os.path.exists(src_data):
        print(f"missing bundle: {src_data} - run tools/extract.py first")
        return 1

    idx = BundleIndex(src_data, cfg.get("table.data_header", 28))
    pages = cfg.ui_pages if args.pages == "all" else \
        [int(x) for x in args.pages.split(",")]

    merged: dict[str, int] = {}
    for pi in pages:
        r = idx.byi.get(pi)
        if not r:
            continue
        group = decompress_range(idx.bundle, r["start"], r["end"])
        got = scan(group, args.min_len)
        print(f"  rec {pi:5d}: {len(got)} candidates ({len(group)} bytes)")
        for k, v in got.items():
            merged[k] = merged.get(k, 0) + v

    rows = sorted(merged.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    out = args.out or os.path.join(cfg.root, "work/strings.tsv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        fh.write("# source\ttranslation\toccurrences\n")
        fh.write("# Fill in column 2. Lines starting with '#' are ignored.\n")
        fh.write("# Delete rows that are control names or asset URLs.\n")
        for text, hits in rows:
            fh.write(f"{text}\t\t{hits}\n")
    print(f"\n{len(rows)} candidates -> {out}")
    print("Translate column 2, then set language.strings to your file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
