#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Survey a bundle: what object types live in which record?

    python tools/probe.py [--config config.json] [--scan-fonts]

Run this FIRST when you port the toolchain to another app version: the record
numbers for fonts and UI pages move between builds, and this finds them for
you instead of you guessing.

It decompresses only the first few KB of every record (enough to read the KNHC
type name), so it is fast even on a 1 GB bundle.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vdl_config import Config
from stride_bundle import BundleIndex, rvint, decompress_range
from stride_font import StrideFont


def partial_decompress(data, start: int, end: int, want: int = 4096):
    """Decompress chunks until we have at least `want` bytes.

    Returns None on records whose chunk stream is malformed or truncated.
    That happens for ~46 of 2120 records in this build even in the *stock*
    bundle, so it is a property of the data, not of this tool.
    """
    out = bytearray()
    p = start
    try:
        while p < end and len(out) < want:
            flags, p = rvint(data, p)
            orig, p = rvint(data, p)
            if flags & 1:
                import lz4.block
                clen, p = rvint(data, p)
                out += lz4.block.decompress(data[p:p + clen], uncompressed_size=orig)
                p += clen
            else:
                out += data[p:p + orig]
                p += orig
    except Exception:
        return None
    return bytes(out)


def type_name(head: bytes) -> str:
    """KNHC type name from a (partial) object group."""
    if head[:4] != b"KNHC":
        return "<not a chunk>"
    p = 8
    tl, p = rvint(head, p)
    try:
        return head[p:p + tl].decode("utf-8", "replace")
    except Exception:
        return "?"


def short(name: str) -> str:
    return name.split(",")[0].split(".")[-1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--scan-fonts", action="store_true",
                    help="also parse every OfflineRasterizedSpriteFont found")
    ap.add_argument("--filter", default=None, help="only list types containing this")
    ap.add_argument("--set", action="append", default=[], metavar="a.b=value")
    args = ap.parse_args()

    cfg = Config.load(args.config, args.set)
    src = cfg.path("bundles.data")
    if not os.path.exists(src):
        print(f"missing bundle: {src} - run tools/extract.py first")
        return 1

    header = cfg.get("table.data_header", 28)
    idx = BundleIndex(src, header)
    print("sanity:", idx.sanity(), "\n")

    kinds = Counter()
    rows = []
    undecodable = 0
    for r in idx.records:
        head = partial_decompress(idx.bundle, r["start"], r["end"])
        if head is None:
            undecodable += 1
            kinds["<undecodable>"] += 1
            rows.append((r["i"], "<undecodable>", r["size_nc"]))
            continue
        tn = short(type_name(head))
        kinds[tn] += 1
        rows.append((r["i"], tn, r["size_nc"]))

    print(f"object types ({len(idx.records)} records):")
    for tn, n in kinds.most_common():
        if args.filter and args.filter.lower() not in tn.lower():
            continue
        print(f"  {n:5d}  {tn}")

    interesting = ("UIPage", "SpriteFont", "Texture", "UIElement", "Page")
    print("\nnotable records:")
    for i, tn, snc in rows:
        if any(k.lower() in tn.lower() for k in interesting):
            print(f"  rec {i:5d}  {tn:45s} size_nc={snc}")

    if args.scan_fonts:
        print("\nfont details (atlas is always font rec + 1):")
        for i, tn, _ in rows:
            # Offline fonts are the rasterised ones; Runtime fonts are built
            # at load time and have a different layout.
            if "offlinerasterizedspritefont" not in tn.lower():
                continue
                try:
                    f = StrideFont(i, idx)
                    s = f.summary()
                    a = f.atlas()
                    print(f"  rec {i:5d} {s['url']:32s} size={s['size']:5.1f} "
                          f"glyphs={s['count']:5d} cjk={s['cjk']:4d} "
                          f"atlas={a['width']}x{a['height']} "
                          f"px_ok={a['total'] == a['expected']}")
                except Exception as exc:
                    print(f"  rec {i:5d}: {type(exc).__name__}: {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
