#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild a Xenko data bundle *and* its index bundle, keeping both tables
byte-consistent.

=============================================================================
THE ROOT CAUSE THIS MODULE EXISTS FOR
=============================================================================
A VD APK contains **two** bundles:

    assets/data/db/bundles/default.<guid>.bundle   <- data  (~1 GB, payloads)
    assets/data/db/bundles/default.bundle          <- index (224 KB)

The index bundle starts with a 45-byte header, then holds a **second copy of
the exact same 2120 x 46-byte record table** (start/end/size_nc/comp, verified
identical record-by-record), and finishes with a URL -> ObjectId map.

The engine locates objects through the INDEX bundle. Rewriting only the data
bundle's table therefore guarantees a crash - every byte offset the loader
uses still points at the old layout. That false trail consumed ~20 build/test
cycles before it was found (see docs/07-pitfalls.md, pitfall #1).

Rule: whenever a payload moves, patch **both** tables in the same run.

size_nc
-------
    SizeNotCompressed = len(decompressed_group) - len(reference_area)

holds for 1254/1255 records, but `group_offsets()` only reads the *first*
KNHC header in a group, so groups holding several objects come out 1 byte
short (record 954 is the known case). We therefore use a **relative delta**:

    new_snc = old_snc + (len(new_group) - len(old_group))

Equivalent wherever the formula holds, and it preserves the original (correct)
value on the odd record out.
"""
from __future__ import annotations

import struct

from stride_bundle import (RECLEN, compress_range, decompress_range,
                           group_offsets, read_table, rvint)


def build(src_data_path: str, src_index_path: str, changes: dict,
          out_data_path: str, out_index_path: str,
          data_header: int = 28, index_header: int = 45,
          index_count_offset: int = 41,
          pad_to_original: bool = False, verbose: bool = True) -> dict:
    """Rewrite object groups.

    changes : {record_index: new_group_bytes}
        `new_group_bytes` is the *decompressed* group (KNHC header + refs +
        object data). Re-compression is done here.

    pad_to_original : keep every span at its original length by appending
        zeros. Useful as a control experiment, not needed in production
        (span length may change - see docs/05-string-patching.md).
    """
    with open(src_data_path, "rb") as fh:
        data = bytearray(fh.read())
    with open(src_index_path, "rb") as fh:
        index = bytearray(fh.read())

    drecs = read_table(data, data_header)
    irecs = read_table(index, index_header, index_count_offset)

    # ---- precondition: the two tables must agree *before* we touch them ----
    assert len(drecs) == len(irecs), (len(drecs), len(irecs))
    for a, b in zip(drecs, irecs):
        assert a["oid"] == b["oid"], f"oid mismatch @{a['i']}"
        assert (a["start"], a["end"], a["size_nc"]) == \
               (b["start"], b["end"], b["size_nc"]), \
               f"table mismatch @{a['i']}: data={a['start'], a['end'], a['size_nc']} " \
               f"index={b['start'], b['end'], b['size_nc']}"
    if verbose:
        print(f"precondition OK: {len(drecs)} records, both tables identical")

    # ---- re-compress every changed group -----------------------------------
    new_streams, new_snc = {}, {}
    for ri, grp in sorted(changes.items()):
        r = drecs[ri]
        old_group = decompress_range(data, r["start"], r["end"])
        refs, _, _ = group_offsets(old_group)
        snc_delta = len(grp) - len(old_group)
        snc = r["size_nc"] + snc_delta
        if verbose and len(old_group) - refs != r["size_nc"]:
            print(f"  note rec{ri}: size_nc formula off by "
                  f"{len(old_group) - refs - r['size_nc']} -> using relative delta")
        new_streams[ri] = compress_range(grp)
        new_snc[ri] = snc
        if verbose:
            print(f"  rec{ri}: span {r['end'] - r['start']} -> "
                  f"{len(new_streams[ri])}, snc {r['size_nc']} -> {snc}")

    # ---- lay the file out again, record by record --------------------------
    out = bytearray()
    out += data[:drecs[0]["start"]]
    newrecs = {}
    for r in drecs:
        ri = r["i"]
        old_span = r["end"] - r["start"]
        if ri in new_streams:
            stream = new_streams[ri]
            if pad_to_original:
                assert len(stream) <= old_span, f"rec{ri} overflows original span"
                payload = stream + b"\x00" * (old_span - len(stream))
            else:
                payload = stream
        else:
            payload = bytes(data[r["start"]:r["end"]])
        new_start = len(out)
        out += payload
        new_end = len(out)
        newrecs[ri] = (new_start, new_end, new_snc.get(ri, r["size_nc"]), r["comp"])

    # ---- patch BOTH tables --------------------------------------------------
    for ri, (st, en, snc, _comp) in newrecs.items():
        for buf, recs in ((out, drecs), (index, irecs)):
            base = recs[ri]["base"]
            struct.pack_into("<q", buf, base + 17, st)
            struct.pack_into("<q", buf, base + 25, en)
            struct.pack_into("<q", buf, base + 33, snc)

    # data bundle header carries its own length; the index bundle must keep its
    # original declared length (224007) because its trailer size is fixed.
    struct.pack_into("<q", out, 4, len(out))

    with open(out_data_path, "wb") as fh:
        fh.write(bytes(out))
    with open(out_index_path, "wb") as fh:
        fh.write(bytes(index))

    rep = dict(data_len=len(out), index_len=len(index),
               delta=len(out) - len(data), changed=list(new_streams))
    if verbose:
        print(f"written: data {len(out)} ({rep['delta']:+}), index {len(index)}")
    return rep


def verify(out_data_path: str, out_index_path: str, expect_changes: dict | None = None,
           data_header: int = 28, index_header: int = 45,
           index_count_offset: int = 41, verbose: bool = True) -> dict:
    """Read-back check: tables agree, records contiguous, everything decodes."""
    with open(out_data_path, "rb") as fh:
        data = fh.read()
    with open(out_index_path, "rb") as fh:
        index = fh.read()

    drecs = read_table(data, data_header)
    irecs = read_table(index, index_header, index_count_offset)
    assert len(drecs) == len(irecs), "record count differs between bundles"

    bad = sum(1 for a, b in zip(drecs, irecs)
              if (a["start"], a["end"], a["size_nc"]) !=
                 (b["start"], b["end"], b["size_nc"]))
    disc = [i for i in range(len(drecs) - 1) if drecs[i]["end"] != drecs[i + 1]["start"]]

    ok = 0
    for r in drecs:
        try:
            g = decompress_range(data, r["start"], r["end"])
            if len(g) >= r["size_nc"]:
                ok += 1
        except Exception:
            pass

    tail_ok = bool(drecs) and drecs[-1]["end"] == len(data)
    if verbose:
        print(f"  verify: table_mismatch={bad} discontinuities={len(disc)} "
              f"decompressible={ok}/{len(drecs)} tail_ok={tail_ok} "
              f"size={len(data)}")
    if expect_changes and verbose:
        for ri, grp in expect_changes.items():
            g = decompress_range(data, drecs[ri]["start"], drecs[ri]["end"])
            print(f"    rec{ri}: decoded {len(g)} expected {len(grp)} "
                  f"identical={bytes(g) == bytes(grp)}")
    return dict(table_mismatch=bad, discontinuities=len(disc),
                decompressed=ok, total=len(drecs), tail_ok=tail_ok)
