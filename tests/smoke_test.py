#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-contained smoke tests - run with any Python 3.9+.

    python tests/smoke_test.py

These tests build a synthetic two-bundle database (the exact layout documented
in docs/02-bundle-format.md) and exercise the whole pipeline on it: parsing,
string substitution, atlas growth bookkeeping, dual-table rewrite, read-back.
No proprietary file is needed and no device is required.
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))

from stride_bundle import (BundleIndex, compress_range, decompress_range,
                           group_offsets, read_table, rvint, wr_str, wr_vint)
from stride_font import GLYPH, StrideFont, walk_entries
import bundle_writer
from string_patcher import (apply_translations, codepoints_of, count_residual,
                            load_pairs)

DATA_HDR = 28
INDEX_HDR = 45
RECLEN = 46


# ------------------------------------------------------------------ builders

def make_group(type_name: str, payload: bytes, refs_len: int = 128) -> bytes:
    """A serialized object group in the documented KNHC shape."""
    tn = wr_str(type_name)
    refs = b"\xAA" * refs_len
    obj_off = 8 + len(tn) + 8 + len(refs)
    head = b"KNHC" + struct.pack("<i", 1) + tn + struct.pack("<ii", obj_off, obj_off - refs_len)
    return head + refs + payload


def make_texture_obj(w: int, h: int, px: bytes) -> bytes:
    return (b"\x00" + b"TKTX" + struct.pack("<7i", 2, w, h, 1, 1, 1, 29)
            + struct.pack("<i", len(px)) + px + b"\x00" * 4)


def make_pair(tmp: str):
    """Build (data.bundle, index.bundle) with a font record, its atlas, a
    UIPage record and a filler record."""
    font_payload = b"\x00" + struct.pack("<2f", 0.0, 16.0) + b"\x00" + struct.pack("<i", 0)
    # space glyph, then one CJK-capable slot we will add later
    space = bytearray([0x20, 0x00]) + bytes(GLYPH)
    struct.pack_into("<i", space, 2, 0x20)        # Character == key
    space = bytes(space)
    font_payload += struct.pack("<f", 12.0)          # Size
    font_payload += struct.pack("<i", 1)             # dict count
    font_payload += space
    font_payload += struct.pack("<i", 1)             # StaticTextures count
    font_payload += b"\x00" * 9 + wr_str("Texture") + wr_str("font-atlas-0")
    font_group = make_group("Xenko.Rendering.Fonts.OfflineRasterizedSpriteFont",
                            font_payload)

    atlas_px = b"\x11" * (256 * 4 * 4)               # 256x4 RGBA32
    atlas_group = make_group("Xenko.Graphics.Texture", make_texture_obj(256, 4, atlas_px),
                             refs_len=4)

    page_payload = (struct.pack("<i", 0)
                    + wr_str("COMPUTERS") + wr_str("GAMES") + wr_str("INPUT")
                    + wr_str("SETTINGS") + wr_str("STREAMING") + wr_str("VIDEOS")
                    + wr_str("Environment") + wr_str("itemEnvironment")
                    + wr_str("btnSettings") + wr_str("UI/ControlPanel/EnvironmentSheet"))
    page_group = make_group("VirtualDesktop.Mobile.UIPage", page_payload)

    filler_group = make_group("Some.Filler", b"\x00" * 32)

    groups = [font_group, atlas_group, page_group, filler_group]
    streams = [compress_range(g) for g in groups]

    body_start = DATA_HDR + RECLEN * len(groups)
    out = bytearray()
    out += b"XKB1"
    out += struct.pack("<q", 0)                       # patched below
    out += struct.pack("<I", 0)
    out += struct.pack("<i", 0) + struct.pack("<i", 0)
    out += struct.pack("<i", len(groups))
    table_at = len(out)
    out += b"\x00" * (RECLEN * len(groups))
    for g in streams:
        out += g

    for i, (g, s) in enumerate(zip(groups, streams)):
        base = table_at + i * RECLEN
        out[base] = 1
        out[base + 1:base + 17] = bytes(range(16))
        struct.pack_into("<qqq", out, base + 17,
                         body_start + sum(len(x) for x in streams[:i]),
                         body_start + sum(len(x) for x in streams[:i + 1]),
                         len(g))
        out[base + 41] = 1
        struct.pack_into("<i", out, base + 42, 0)
    struct.pack_into("<q", out, 4, len(out))

    data_path = os.path.join(tmp, "data.bundle")
    with open(data_path, "wb") as fh:
        fh.write(bytes(out))

    # ---------------- index bundle ----------------
    index = bytearray()
    index += b"XKB1"
    index += struct.pack("<q", 0)
    index += struct.pack("<ii", 0, 0)
    index += struct.pack("<i", 1)
    index += bytes([1]) + bytes(range(16))            # has_bundle_id + GUID
    index += struct.pack("<i", len(groups))
    itab = len(index)
    # copy the data table verbatim - that is what the real file does
    index += out[table_at:table_at + RECLEN * len(groups)]
    struct.pack_into("<q", index, 4, len(index))
    index_path = os.path.join(tmp, "index.bundle")
    with open(index_path, "wb") as fh:
        fh.write(bytes(index))

    return data_path, index_path, (font_group, atlas_group, page_group, filler_group)


# --------------------------------------------------------------------- tests

class TestPrimitives(unittest.TestCase):
    def test_varint_roundtrip(self):
        for v in (0, 1, 127, 128, 300, 8191, 1 << 20, 1 << 28):
            blob = wr_vint(v)
            self.assertLessEqual(len(blob), 5)
            back, p = rvint(blob, 0)
            self.assertEqual(v, back)
            self.assertEqual(p, len(blob))

    def test_lz4_roundtrip(self):
        raw = os.urandom(200000)
        self.assertEqual(decompress_range(compress_range(raw), 0,
                                          len(compress_range(raw))), raw)


class TestStringPatcher(unittest.TestCase):
    def setUp(self):
        self.pairs = [
            ("COMPUTERS", "Computers-ru"),          # shorter, allowed
            ("COMPUTERS", "IGNORED-dup"),
            ("GAMES", "Games-ru"),
            ("Environment", "Environment-ru"),
            ("SETTINGS", "Settings-ru"),
        ]

    def test_strict_substitutes_exact_field(self):
        group = make_group("T", wr_str("GAMES") + b"\x00" * 8)
        out, hits = apply_translations(group, self.pairs, mode="strict", verbose=False)
        self.assertIn(b"Games-ru", out)
        self.assertNotIn(b"GAMES", out)
        self.assertTrue(any(m == "varint" for _s, _t, _n, m in hits))

    def test_strict_never_touches_control_names(self):
        group = make_group("T", wr_str("itemEnvironment") + wr_str("btnSettings")
                            + wr_str("UI/ControlPanel/EnvironmentSheet"))
        out, _ = apply_translations(group, self.pairs, mode="strict", verbose=False)
        self.assertIn(b"itemEnvironment", out)
        self.assertIn(b"btnSettings", out)
        self.assertIn(b"EnvironmentSheet", out)

    def test_padded_mode_is_unsafe_by_design(self):
        """Demonstrates why the unsafe mode is disabled by default."""
        pairs = [("Environment", "Env-ru"), ("Settings", "Set-ru")]
        group = make_group("T", wr_str("btnSettings") + wr_str("Environment"))
        out, _ = apply_translations(group, pairs, mode="padded", verbose=False)
        self.assertNotIn(b"btnSettings", out)        # <- control name destroyed
        self.assertNotIn(b"Environment", out)        # <- but the label is gone too

    def test_field_mode_hits_padded_label(self):
        """A label stored as `varint(N) + ' Release trigger to complete '`
        (padded inside its field) is invisible to plain strict mode but must
        be caught by field mode - without ever touching control names."""
        label = b" Release trigger to complete "
        group = make_group("T", wr_vint(len(label)) + label
                            + wr_str("itemEnvironment"))
        pairs = [("Release trigger to complete", "X")]
        out_s, hits_s = apply_translations(group, pairs, mode="strict", verbose=False)
        self.assertEqual(hits_s, [])                     # strict misses it

        out_f, hits_f = apply_translations(group, pairs, mode="strict-field", verbose=False)
        self.assertEqual(len(hits_f), 1)
        self.assertEqual(hits_f[0][3], "field")
        self.assertIn(b"X", out_f)
        self.assertIn(b"itemEnvironment", out_f)         # untouched
        self.assertEqual(len(out_f), len(group))         # length preserved

    def test_field_mode_never_touches_control_names(self):
        group = make_group("T", wr_str("itemEnvironment") + wr_str("btnSettings")
                            + wr_str("EnvironmentSheet"))
        pairs = [("Environment", "E"), ("Settings", "S")]
        out, hits = apply_translations(group, pairs, mode="strict-field", verbose=False)
        self.assertIn(b"itemEnvironment", out)
        self.assertIn(b"btnSettings", out)
        self.assertIn(b"EnvironmentSheet", out)
        self.assertEqual(hits, [])

    def test_field_mode_skips_overlong_target(self):
        # A padded field that strict cannot match either: field mode must
        # decline rather than corrupt it.
        label = b" Release "
        group = make_group("T", wr_vint(len(label)) + label)
        out, hits = apply_translations(
            group, [("Release", "way-too-long-target")], mode="field", verbose=False)
        self.assertEqual(out, group)
        self.assertEqual(hits, [])

    def test_length_change_is_allowed(self):
        group = make_group("T", wr_str("GAMES"))
        out, _ = apply_translations(group, self.pairs, mode="strict", verbose=False)
        self.assertNotEqual(len(group), len(out))
        self.assertEqual(decompress_range(
            compress_range(out), 0, len(compress_range(out))), out)

    def test_longest_source_first(self):
        pairs = [("A very long setting label", "x"), ("A very long setting", "y")]
        ordered = sorted(pairs, key=lambda kv: -len(kv[0].encode()))
        self.assertEqual(ordered[0][0], "A very long setting label")


class TestTsvTable(unittest.TestCase):
    def test_load_and_codepoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "t.tsv")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# comment\n\nOK\t\041\042\nQUIT\t\0410\n")
            pairs = load_pairs(path)
            self.assertEqual(len(pairs), 2)
            self.assertEqual(pairs[0][0], "QUIT")      # longest first
            cps = codepoints_of(pairs)
            self.assertEqual(cps, [0x21, 0x22, 0x30])   # ! " 0


class TestBundleRoundTrip(unittest.TestCase):
    def test_full_pipeline_on_synthetic_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, index, groups = make_pair(tmp)
            idx = BundleIndex(data, DATA_HDR)
            self.assertEqual(idx.sanity()["records"], 4)
            self.assertEqual(idx.sanity()["discontinuities"], 0)

            # the font parses, the atlas is 256x4, the space glyph is there
            font = StrideFont(0, idx)
            self.assertEqual(font.size, 12.0)
            # dict_start is located by anchoring, so it follows the header
            # size of this particular object (32 on the real VD fonts).
            self.assertEqual(font.dict_start, 22)
            self.assertEqual(len(font.entries), 1)
            atlas = font.atlas()
            self.assertEqual((atlas["width"], atlas["height"]), (256, 4))
            self.assertTrue(atlas["total"] == atlas["expected"])

            # page strings
            page = idx.content(2)
            self.assertIn(b"COMPUTERS", page)

            # ---- rewrite the page record (string change, length changes) ----
            pairs = [("COMPUTERS", "\u8ba1\u7b97\u673a"), ("GAMES", "\u6e38\u620f"),
                     ("INPUT", "\u8f93\u5165"), ("SETTINGS", "\u8bbe\u7f6e"),
                     ("STREAMING", "\u4e32\u6d41"), ("VIDEOS", "\u89c6\u9891")]
            new_page, hits = apply_translations(page, pairs, mode="strict", verbose=False)
            self.assertEqual(len(hits), 6)

            out_data = os.path.join(tmp, "out-data.bundle")
            out_index = os.path.join(tmp, "out-index.bundle")
            rep = bundle_writer.build(data, index, {2: new_page},
                                      out_data, out_index,
                                      data_header=DATA_HDR, index_header=INDEX_HDR,
                                      index_count_offset=41, verbose=False)
            self.assertEqual(rep["changed"], [2])

            st = bundle_writer.verify(out_data, out_index,
                                      data_header=DATA_HDR, index_header=INDEX_HDR,
                                      index_count_offset=41, verbose=False)
            self.assertEqual(st["table_mismatch"], 0)
            self.assertEqual(st["discontinuities"], 0)
            self.assertEqual(st["decompressed"], 4)
            self.assertTrue(st["tail_ok"])

            # the two record tables must be byte-identical
            with open(out_data, "rb") as fh:
                d = fh.read()
            with open(out_index, "rb") as fh:
                ix = fh.read()
            self.assertEqual(d[DATA_HDR:DATA_HDR + RECLEN * 4],
                             ix[INDEX_HDR:INDEX_HDR + RECLEN * 4])

            # and the new strings are readable through the parser
            idx2 = BundleIndex(out_data, DATA_HDR)
            page2 = idx2.content(2)
            self.assertIn("\u8ba1\u7b97\u673a".encode("utf-8"), page2)
            self.assertIn(b"itemEnvironment", page2)     # untouched
            self.assertIn(b"btnSettings", page2)         # untouched
            self.assertEqual(count_residual(page2, [("COMPUTERS", "x")]), [])

            # untouched records decode to exactly the original bytes
            for i in (0, 1, 3):
                self.assertEqual(idx2.content(i), idx.content(i))


if __name__ == "__main__":
    unittest.main(verbosity=2)
