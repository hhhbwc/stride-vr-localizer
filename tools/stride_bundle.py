#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Low-level reader for Xenko / Stride (`.bundle`) object databases.

Reverse-engineered from Virtual Desktop 1.34.22.0 (PICO 4 build), engine
Stride (Xenko) 3.2.0.2, .NET NativeAOT - no managed metadata, so every
layout below was derived from bytes on disk and cross-checked against the
Stride source (LZ4Stream.cs) plus a fully-localised Quest 2 build.

Header
------
    [0:4]   "XKB1" magic (u32 LE 0x31424B58)
    [4:12]  i64  total file length
    [12:16] u32  crc (always 0 - unused)
    [16:20] i32  dependencies count
    [20:24] i32  incremental bundle count
    [24:28] i32  object record count
    [28:..] count * 46-byte object records

Object record (46 bytes) - see docs/02-bundle-format.md
-------------------------
    +0    u8   has_id (always 1 in practice)
    +1    16B  ObjectId (MurmurHash3-128 of the object content)
    +17   i64  StartOffset        (byte offset of the compressed payload)
    +25   i64  EndOffset          (exclusive)
    +33   i64  SizeNotCompressed
    +41   u8   IsCompressed
    +42   i32  IncrementalBundleIndex

    Records are stored back-to-back: end[i] == start[i+1].

    >>> Getting this table wrong is the classic trap: an early version of
    >>> this code treated EndOffset as StartOffset and produced months of
    >>> fake mysteries ("size_nc semantics unknown", "delta 0/4/128").

Payload framing = Stride LZ4Stream chunk stream
-----------------------------------------------
    chunk := varint(flags) + varint(originalLength)
             + [varint(compressedLength) if flags & 1] + data
    flags bit0 = compressed, bit1 = high compression, bits 2-4 = passes.
    Chunk size 1 MiB; compression is plain LZ4 block (python-lz4 compatible).

Object payload = serialised chunk
---------------------------------
    "KNHC" (i32 LE) + version(i32=1)
    + TypeName (7-bit length + utf8, assembly-qualified)
    + OffsetToObject (i32) + OffsetToReferences (i32)
    + [reference area] + [object data]
"""
from __future__ import annotations

import os
import struct

import lz4.block

RECLEN = 46


# ------------------------------------------------------------------ varints

def rvint(d, p: int) -> tuple[int, int]:
    """Read a 7-bit little-endian varint at p. Returns (value, next_p)."""
    shift = 0
    value = 0
    while True:
        b = d[p]
        p += 1
        value |= (b & 0x7F) << shift
        if not (b & 0x80):
            return value, p
        shift += 7


def wr_vint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def wr_str(s: str) -> bytes:
    b = s.encode("utf-8")
    return wr_vint(len(b)) + b


# -------------------------------------------------------------- compression

def decompress_range(data, start: int, end: int) -> bytes:
    """Decompress every LZ4Stream chunk in [start, end)."""
    p = start
    out = bytearray()
    while p < end:
        flags, p = rvint(data, p)
        orig, p = rvint(data, p)
        if flags & 1:
            clen, p = rvint(data, p)
            out += lz4.block.decompress(data[p:p + clen], uncompressed_size=orig)
            p += clen
        else:
            out += data[p:p + orig]
            p += orig
    return bytes(out)


def compress_range(raw: bytes, chunk_size: int = 1 << 20) -> bytes:
    """Re-compress with the same 1 MiB LZ4 framing the loader expects."""
    out = bytearray()
    for off in range(0, len(raw), chunk_size):
        chunk = raw[off:off + chunk_size]
        c = lz4.block.compress(chunk, store_size=False, mode="high_compression")
        out += wr_vint(1) + wr_vint(len(chunk)) + wr_vint(len(c)) + c
    return bytes(out)


# ---------------------------------------------------------------- the index

class BundleIndex:
    """Parses the object record table of a data bundle.

    Parameters
    ----------
    bundle_path : path to the *data* bundle (the big one).
    header     : byte offset of the record table (28 on VD 1.34.22.0).
    """

    def __init__(self, bundle_path: str, header: int = 28):
        self.bundle_path = os.path.abspath(bundle_path)
        with open(bundle_path, "rb") as fh:
            self.bundle = fh.read()
        self.header = header

        n = struct.unpack_from("<i", self.bundle, header - 4)[0]
        self.records = []
        for i in range(n):
            base = header + i * RECLEN
            has_id = self.bundle[base]
            oid = self.bundle[base + 1:base + 17].hex() if has_id else ""
            start, end, size_nc = struct.unpack_from("<qqq", self.bundle, base + 17)
            self.records.append({
                "i": i,
                "base": base,
                "oid": oid,
                "has_id": has_id,
                "start": start,
                "end": end,
                "size_nc": size_nc,
                "comp": self.bundle[base + 41],
                "incr": struct.unpack_from("<i", self.bundle, base + 42)[0],
            })
        self.byi = {r["i"]: r for r in self.records}

    def __len__(self) -> int:
        return len(self.records)

    def record(self, i: int) -> dict:
        return self.byi[i]

    def next_start(self, i: int) -> int:
        return self.byi[i]["end"]

    def content(self, i: int, cap: int | None = None) -> bytes:
        """Decompressed object group for record i (KNHC header included)."""
        r = self.byi[i]
        d = decompress_range(self.bundle, r["start"], self.next_start(i))
        return d[:cap] if cap else d

    def raw_span(self, i: int) -> bytes:
        r = self.byi[i]
        return self.bundle[r["start"]:r["end"]]

    def sanity(self) -> dict:
        """Cheap structural self-check - run this before you trust anything."""
        recs = self.records
        contig = sum(1 for a, b in zip(recs, recs[1:]) if a["end"] != b["start"])
        ordered = all(r["start"] < r["end"] for r in recs)
        return {
            "records": len(recs),
            "start_lt_end": ordered,
            "discontinuities": contig,
            "tail_ok": bool(recs) and recs[-1]["end"] == len(self.bundle),
        }


# ---------------------------------------------------------- object plumbing

def group_offsets(group: bytes) -> tuple[int, int, int]:
    """Parse a decompressed group -> (refs_len, OffsetToObject, OffsetToReferences).

    `group` must start with the KNHC chunk header. Note that a group may hold
    more than one object; this only inspects the *first* KNHC header, which is
    enough for the size_nc bookkeeping used by bundle_writer.
    """
    p = 8                      # skip "KNHC" + version
    tl, p = rvint(group, p)    # type name (7-bit len + utf8)
    p += tl
    obj_off, ref_off = struct.unpack_from("<ii", group, p)
    return obj_off - ref_off, obj_off, ref_off


def read_table(buf, header: int, count_offset: int | None = None) -> list[dict]:
    """Generic record-table reader (used for both the data and index bundle)."""
    if count_offset is None:
        n = struct.unpack_from("<i", buf, header - 4)[0]
    else:
        n = struct.unpack_from("<i", buf, count_offset)[0]
    recs = []
    for i in range(n):
        base = header + RECLEN * i
        has_id = buf[base]
        oid = buf[base + 1:base + 17].hex() if has_id else ""
        st, en, snc = struct.unpack_from("<qqq", buf, base + 17)
        recs.append(dict(i=i, base=base, oid=oid, has_id=has_id, start=st,
                         end=en, size_nc=snc, comp=buf[base + 41],
                         incr=struct.unpack_from("<i", buf, base + 42)[0]))
    return recs
