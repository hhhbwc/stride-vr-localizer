#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Main build: patch UI strings + rebuild font atlases + rewrite both bundles.

    python tools/build.py [--config config.json] [--tag T23]
                          [--pages all|none|954,869] [--mode strict]
                          [--set device.serial=ABC]

Pipeline
--------
    1. read config (+ CLI overrides)
    2. load translation table (TSV) -> codepoints that must exist in the atlas
    3. per font record: rasterise missing glyphs, grow the atlas, rebuild the
       font object and its paired Texture object
    4. per UI page record: substitute strings (strict varint mode)
    5. rewrite the data bundle *and* the index bundle (both record tables)
    6. verify: tables agree, everything decodes, fonts parse, no residuals
"""
from __future__ import annotations

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vdl_config import Config
from stride_bundle import BundleIndex, decompress_range, group_offsets, rvint
from stride_font import StrideFont, walk_entries
from bundle_writer import build as write_bundles, verify as verify_bundles
import atlas_repack
from string_patcher import apply_translations, codepoints_of, count_residual, load_pairs


# ------------------------------------------------------------------ fonts

def make_font_groups(idx, rec: int, weight: str, ttf: str, codepoints):
    """Rebuild the font object (rec) and its atlas texture (rec + 1)."""
    f = StrideFont(rec, idx)
    a = f.atlas()
    assert a["total"] == a["expected"], f"rec{rec}: atlas size field corrupt"
    # dict_start is located dynamically (32 on VD 1.34.22.0) - never hard-code it.
    assert f.dict_start > 14, f"rec{rec}: implausible dict_start={f.dict_start}"
    assert f.count_matches, f"rec{rec}: dict count {f.count} != {len(f.entries)}"

    res = atlas_repack.build(idx, rec, ttf, codepoints, weight)
    new_h, new_px = res.new_h, res.atlas

    od = f.od
    count_off = f.dict_start - 4
    have = {e["key"] for e in f.entries}

    old_entries = [(e["key"], e["keyraw"] + e["glyph"]) for e in f.entries]
    new_entries = [(e["key"], chr(e["key"]).encode("utf-8") + b"\x00" + e["glyph"])
                   for e in res.entries]
    dup = have & {k for k, _ in new_entries}
    assert not dup, f"rec{rec}: duplicate codepoints {sorted(dup)[:10]}"

    all_entries = sorted(old_entries + new_entries, key=lambda t: t[0])

    new_od = bytearray()
    new_od += od[:count_off]
    new_od += struct.pack("<i", len(all_entries))
    for _key, blob in all_entries:
        new_od += blob
    new_od += od[f.dict_end:]

    fg = idx.content(rec)
    _refs, oo_f, _rr = group_offsets(fg)
    new_font_group = fg[:oo_f] + bytes(new_od)

    # read the result back through the same parser we trust
    od2 = new_font_group[oo_f:]
    e2, _ = walk_entries(od2, f.dict_start)
    assert len(e2) == len(all_entries), "font dict readback mismatch"
    assert struct.unpack_from("<i", od2, count_off)[0] == len(all_entries), "count mismatch"

    # ---------------- atlas texture ----------------
    arec = rec + 1
    tg = idx.content(arec)
    p = 8
    tl, p = rvint(tg, p)
    p += tl
    oo_t, _rr_t = struct.unpack_from("<ii", tg, p)
    obj = tg[oo_t:]
    assert obj[0] == 0 and obj[1:5] == b"TKTX", "not a TKTX texture"
    dim, w, h, depth, arr, mips, fmt = struct.unpack_from("<7i", obj, 5)
    old_total = struct.unpack_from("<i", obj, 33)[0]
    old_tail = obj[37 + old_total:]          # keep whatever was there (4B or 8B)
    assert (w, h, old_total) == (a["width"], a["height"], a["total"])
    assert len(new_px) == a["width"] * new_h * 4

    new_obj = bytearray()
    new_obj += obj[:5]
    new_obj += struct.pack("<7i", dim, a["width"], new_h, depth, arr, mips, fmt)
    new_obj += struct.pack("<i", a["width"] * new_h * 4)
    new_obj += new_px
    new_obj += old_tail
    new_atlas_group = tg[:oo_t] + bytes(new_obj)

    print(f"  rec{rec} ({weight}): dict {len(old_entries)} -> {len(all_entries)}, "
          f"atlas {a['width']}x{a['height']} -> {a['width']}x{new_h}, "
          f"tail {len(old_tail)}B")
    return rec, new_font_group, arec, new_atlas_group


# ------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--tag", default="build", help="output tag (work/<tag>-*.bundle)")
    ap.add_argument("--pages", default="all", help="all | none | 954,869")
    ap.add_argument("--mode", default="strict",
                    choices=["strict", "equal", "padded", "field", "strict-field"],
                    help="substitution mode; strict and strict-field are the "
                         "safe ones (see docs/05-string-patching.md)")
    ap.add_argument("--set", action="append", default=[], metavar="a.b=value")
    args = ap.parse_args()

    cfg = Config.load(args.config, args.set)
    src_data = cfg.path("bundles.data")
    src_index = cfg.path("bundles.index")
    for p in (src_data, src_index):
        if not os.path.exists(p):
            print(f"missing bundle: {p}\nrun: python tools/extract.py --config {args.config}")
            return 1

    out_data = os.path.join(cfg.root, f"work/{args.tag}-data.bundle")
    out_index = os.path.join(cfg.root, f"work/{args.tag}-index.bundle")

    # ---- 1. translations ---------------------------------------------------
    strings_path = cfg.path("language.strings")
    pairs = load_pairs(strings_path)
    cps = codepoints_of(pairs, cfg.get("language.extra_glyphs", ""))
    print(f"translations: {len(pairs)} entries from {strings_path}")
    print(f"codepoints required: {len(cps)}")

    if args.mode not in ("strict", "strict-field", "field"):
        print(f"!! mode={args.mode} is UNSAFE (bare text replace corrupts "
              f"control names). Use --mode strict or --mode strict-field.")

    idx = BundleIndex(src_data, cfg.get("table.data_header", 28))
    print("bundle sanity:", idx.sanity())

    changes: dict[int, bytes] = {}

    # ---- 2. UI strings -----------------------------------------------------
    all_pages = cfg.ui_pages
    if args.pages == "none":
        pages: list[int] = []
    elif args.pages == "all":
        pages = all_pages
    else:
        pages = [int(x) for x in args.pages.split(",")]

    print("\n[1/3] UI strings")
    total_hits = 0
    for pi in pages:
        r = idx.byi.get(pi)
        if not r:
            print(f"  rec {pi}: not present in this bundle, skipped")
            continue
        group = decompress_range(idx.bundle, r["start"], r["end"])
        new_group, hits = apply_translations(group, pairs, mode=args.mode)
        if hits:
            changes[pi] = new_group
            print(f"  rec {pi:5d}: {len(hits)} strings / "
                  f"{sum(h[2] for h in hits)} occurrences "
                  f"({len(group)} -> {len(new_group)} B)")
            total_hits += sum(h[2] for h in hits)
    print(f"  total substitutions: {total_hits}")

    # ---- 3. fonts ----------------------------------------------------------
    print("\n[2/3] Font atlases")
    for rec, weight in cfg.font_recs:
        ttf = cfg.ttf(weight)
        if not os.path.exists(ttf):
            print(f"  rec{rec}: missing font file {ttf} - skipped")
            continue
        fr, fg, ar, ag = make_font_groups(idx, rec, weight, ttf, cps)
        changes[fr] = fg
        changes[ar] = ag

    # ---- 4. write both bundles ---------------------------------------------
    print("\n[3/3] Rewrite bundles (data + index)")
    rep = write_bundles(src_data, src_index, changes, out_data, out_index,
                        data_header=cfg.get("table.data_header", 28),
                        index_header=cfg.get("table.index_header", 45),
                        index_count_offset=cfg.get("table.index_count_offset", 41))
    print("build:", rep)
    verify_bundles(out_data, out_index, expect_changes=changes,
                   data_header=cfg.get("table.data_header", 28),
                   index_header=cfg.get("table.index_header", 45),
                   index_count_offset=cfg.get("table.index_count_offset", 41))

    # ---- 5. read-back ------------------------------------------------------
    print("\nRead-back")
    idx2 = BundleIndex(out_data, cfg.get("table.data_header", 28))
    for rec, weight in cfg.font_recs:
        try:
            f = StrideFont(rec, idx2)
            a = f.atlas()
            W, H = a["width"], a["height"]
            cjk = sum(1 for e in f.entries if 0x4E00 <= e["key"] <= 0x9FFF)
            oob = sum(1 for e in f.entries
                      if e["sub"][0] + e["sub"][2] > W or e["sub"][1] + e["sub"][3] > H)
            print(f"  rec{rec}: glyphs={len(f.entries)} cjk={cjk} "
                  f"atlas={W}x{H} pixels_ok={a['total'] == a['expected']} "
                  f"out_of_bounds={oob}")
        except Exception as exc:
            print(f"  rec{rec}: READBACK FAILED {type(exc).__name__}: {exc}")

    print("\nResidual English (usually control-name substrings - verify manually)")
    left_total = 0
    for pi in pages:
        r = idx2.byi.get(pi)
        if not r:
            continue
        g = decompress_range(idx2.bundle, r["start"], r["end"])
        for src, n in count_residual(g, pairs):
            left_total += n
            print(f"  rec {pi} still contains x{n}: {src[:60]!r}")
    print(f"  -> {left_total} residual occurrences")
    print(f"\noutput: {out_data}\n        {out_index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
