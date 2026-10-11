#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Swap one STORED entry inside an APK without re-signing it.

    python tools/apksurgery.py --src work/base.apk \
        --new-bundle work/build-data.bundle \
        --entry assets/data/db/bundles/default.<guid>.bundle \
        --dst work/out.apk

Why this exists
---------------
`adb install` on a re-signed APK is rejected by Virtual Desktop's licence
check. The way through is to keep the original signature untouched and replace
the payload the way the system itself would: keep every local header, every
unmodified byte, and the APK Signing Block verbatim; only rewrite the target
entry's data plus the CRC/size/offset fields that must change. The resulting
file is installed by copying it over the app's `base.apk` as root, so Android
never re-verifies the signature.

The copied signing block therefore carries STALE content digests. That is fine
for root-side replacement (the platform does not re-hash on launch) but means
the file is NOT installable through the normal installer. Don't try.
"""
from __future__ import annotations

import argparse
import hashlib
import mmap
import os
import struct
import sys
import zipfile
import zlib

CHUNK = 1 << 20


def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]


def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]


def find_eocd(data):
    pos = data.rfind(b"PK\x05\x06")
    assert pos >= 0, "EOCD not found"
    comment_len = u16(data, pos + 20)
    assert pos + 22 + comment_len == len(data), "trailing bytes after EOCD"
    return pos


def locate_signing_block(data, cd_off):
    assert data[cd_off - 16:cd_off] == b"APK Sig Block 42", "APK Signing Block missing"
    size = struct.unpack_from("<Q", data, cd_off - 24)[0]
    start = cd_off - size - 8
    assert start >= 0, "invalid APK Signing Block start"
    assert struct.unpack_from("<Q", data, start)[0] == size, "size mismatch"
    return start, cd_off, data[start:cd_off]


def parse_central_directory(data, eocd_pos):
    count = u16(data, eocd_pos + 10)
    cd_size = u32(data, eocd_pos + 12)
    cd_off = u32(data, eocd_pos + 16)
    assert cd_off + cd_size == eocd_pos, "central directory does not end at EOCD"

    entries = []
    p = cd_off
    for index in range(count):
        assert data[p:p + 4] == b"PK\x01\x02", f"bad central entry {index}"
        nlen, elen, clen = struct.unpack_from("<HHH", data, p + 28)
        end = p + 46 + nlen + elen + clen
        name = data[p + 46:p + 46 + nlen].decode("utf-8")
        entries.append({
            "index": index, "cd_off": p, "cd_end": end, "name": name,
            "flags": u16(data, p + 8), "method": u16(data, p + 10),
            "crc": u32(data, p + 16), "csize": u32(data, p + 20),
            "usize": u32(data, p + 24), "lho": u32(data, p + 42),
        })
        p = end
    assert p == cd_off + cd_size, "central directory size mismatch"
    return entries, cd_off, cd_size


def parse_local_entry(data, entry):
    lh = entry["lho"]
    assert data[lh:lh + 4] == b"PK\x03\x04", f"bad local header for {entry['name']}"
    nlen, elen = struct.unpack_from("<HH", data, lh + 26)
    name = data[lh + 30:lh + 30 + nlen].decode("utf-8")
    assert name == entry["name"], f"local/central name mismatch: {name}"
    assert u16(data, lh + 6) == entry["flags"], "local flags mismatch"
    assert u16(data, lh + 8) == entry["method"], "local method mismatch"
    assert not (entry["flags"] & 0x08), f"data descriptor unsupported: {name}"
    data_start = lh + 30 + nlen + elen
    data_end = data_start + entry["csize"]
    assert data_end <= len(data), f"local payload outside APK: {name}"
    entry.update({"local_start": lh, "local_header_end": data_start,
                  "local_end": data_end})
    return data[lh:data_start]


def patch_local_header(header, crc, csize, usize):
    out = bytearray(header)
    assert out[:4] == b"PK\x03\x04"
    struct.pack_into("<I", out, 14, crc)
    struct.pack_into("<I", out, 18, csize)
    struct.pack_into("<I", out, 22, usize)
    return bytes(out)


def patch_central_header(header, crc, csize, usize, lho):
    out = bytearray(header)
    assert out[:4] == b"PK\x01\x02"
    struct.pack_into("<I", out, 16, crc)
    struct.pack_into("<I", out, 20, csize)
    struct.pack_into("<I", out, 24, usize)
    struct.pack_into("<I", out, 42, lho)
    return bytes(out)


def copy_span(src, out, start, end):
    pos = start
    while pos < end:
        nxt = min(pos + CHUNK, end)
        out.write(src[pos:nxt])
        pos = nxt


def file_crc32(path):
    crc = 0
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            size += len(chunk)
    return size, crc & 0xFFFFFFFF


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_target_stream(apk_path, entry_name, replacement_path):
    with zipfile.ZipFile(apk_path, "r") as zf:
        bad = zf.testzip()
        assert bad is None, f"ZIP CRC/decompression failure: {bad}"
        info = zf.getinfo(entry_name)
        assert info.compress_type == zipfile.ZIP_STORED
        replacement_size, replacement_crc = file_crc32(replacement_path)
        assert info.file_size == replacement_size == info.compress_size
        assert info.CRC == replacement_crc
        h = hashlib.sha256()
        read_size = 0
        with zf.open(info, "r") as src:
            while True:
                chunk = src.read(CHUNK)
                if not chunk:
                    break
                h.update(chunk)
                read_size += len(chunk)
        assert read_size == replacement_size
        assert h.hexdigest() == file_sha256(replacement_path), "readback mismatch"
        print(f"zipfile check: {len(zf.infolist())} entries OK; payload matches")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=os.environ.get("APKSURGERY_SRC", "work/base.apk"))
    ap.add_argument("--new-bundle", default=os.environ.get("APKSURGERY_NEW_BUNDLE"))
    ap.add_argument("--dst", default=os.environ.get("APKSURGERY_DST", "work/out.apk"))
    ap.add_argument("--entry", default=os.environ.get("APKSURGERY_ENTRY"))
    args = ap.parse_args()

    if not args.new_bundle or not args.entry:
        print("--new-bundle and --entry are required")
        return 2

    source_size = os.path.getsize(args.src)
    replacement_size, replacement_crc = file_crc32(args.new_bundle)
    print(f"source:      {args.src} ({source_size} bytes)")
    print(f"replacement: {args.new_bundle} ({replacement_size} bytes)")

    with open(args.src, "rb") as sf, open(args.new_bundle, "rb") as rf:
        with mmap.mmap(sf.fileno(), 0, access=mmap.ACCESS_READ) as source, \
                mmap.mmap(rf.fileno(), 0, access=mmap.ACCESS_READ) as replacement:
            eocd_pos = find_eocd(source)
            entries, cd_off, cd_size = parse_central_directory(source, eocd_pos)
            v2_start, v2_end, signing_block = locate_signing_block(source, cd_off)
            print(f"EOCD @{eocd_pos}: central dir @{cd_off} size={cd_size} "
                  f"entries={len(entries)}")
            print(f"APK Signing Block @{v2_start}..{v2_end} ({len(signing_block)} bytes)")

            target = next((e for e in entries if e["name"] == args.entry), None)
            assert target is not None, f"target entry not found: {args.entry}"
            for entry in entries:
                parse_local_entry(source, entry)
            assert target["method"] == 0, f"target is not STORED: {target['method']}"
            assert target["flags"] == 0, f"unexpected flags: {target['flags']:#x}"
            assert target["csize"] == target["usize"]
            print(f"target #{target['index']}: STORED {target['csize']} bytes")

            local_order = sorted(entries, key=lambda e: e["local_start"])
            assert [e["index"] for e in local_order] == list(range(len(entries)))
            assert local_order[0]["local_start"] == 0
            assert local_order[-1]["local_end"] <= v2_start
            for before, after in zip(local_order, local_order[1:]):
                assert before["local_end"] <= after["local_start"]

            delta = replacement_size - target["csize"]
            print(f"replacement delta: {delta:+}, crc={replacement_crc:08x}")

            new_entries = {}
            temp_dst = args.dst + ".tmp"
            with open(temp_dst, "wb") as out:
                cursor = 0
                for entry in local_order:
                    copy_span(source, out, cursor, entry["local_start"])
                    new_lho = out.tell()
                    local_header = bytes(
                        source[entry["local_start"]:entry["local_header_end"]])
                    if entry is target:
                        local_header = patch_local_header(
                            local_header, replacement_crc,
                            replacement_size, replacement_size)
                    out.write(local_header)
                    if entry is target:
                        out.write(replacement)
                    else:
                        copy_span(source, out, entry["local_header_end"],
                                  entry["local_end"])
                    new_entries[entry["index"]] = {
                        "entry": entry, "lho": new_lho,
                        "crc": replacement_crc if entry is target else entry["crc"],
                        "csize": replacement_size if entry is target else entry["csize"],
                        "usize": replacement_size if entry is target else entry["usize"],
                    }
                    cursor = entry["local_end"]
                copy_span(source, out, cursor, v2_start)
                v2_new_start = out.tell()
                out.write(signing_block)
                cd_new_off = out.tell()

                cd_buf = bytearray()
                for entry in entries:
                    item = new_entries[entry["index"]]
                    raw_cd = bytes(source[entry["cd_off"]:entry["cd_end"]])
                    cd_buf += patch_central_header(
                        raw_cd, item["crc"], item["csize"], item["usize"], item["lho"])
                out.write(cd_buf)

                eocd = bytearray(source[eocd_pos:])
                struct.pack_into("<I", eocd, 12, len(cd_buf))
                struct.pack_into("<I", eocd, 16, cd_new_off)
                out.write(eocd)
                out.flush()
                os.fsync(out.fileno())

            output_size = os.path.getsize(temp_dst)
            assert output_size == source_size + delta
            os.replace(temp_dst, args.dst)
            print(f"written: {args.dst} ({output_size} bytes, {output_size - source_size:+})")
            print(f"central directory moved: {cd_off} -> {cd_new_off}")

    verify_target_stream(args.dst, args.entry, args.new_bundle)
    print(f"output sha256: {file_sha256(args.dst)}")
    print("NOTE: signing block digests are stale by design - install by root "
          "file replacement, never with adb install.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
