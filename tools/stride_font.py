#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reader/writer for Xenko/Stride `OfflineRasterizedSpriteFont` objects.

===========================================================================
VERIFIED BINARY LAYOUT (asserted across all 8 fonts shipped in VD 1.34.22.0)
===========================================================================

Object data `od` = decompressed_group[OffsetToObject:] :

    od[0]        1 byte   root SerializeClassFlags (== 0)
    od[1:5]      float    BaseOffsetY
    od[5:9]      float    DefaultLineSpacing
    od[9]        1 byte   KerningMap presence flag
    od[10:...]            KerningMap = Dictionary<int,float>:
                              int32 count, then count * (int32 key, float value)
    ...                   Size (float)
    ...                   CharacterToGlyph count (int32)   <- offset VARIES
    ...                   CharacterToGlyph entries:
                              [ key: UTF-8(codepoint) + 0x00 ][ glyph: 36B ]
                          glyph(36B) = Character(i32) Subrect(4xi32)
                                       Offset(2xf32) XAdvance(f32)
                                       BitmapIndex(i32)
    ...                   StaticTextures = List<Texture>: int32 count, then
                              per texture: 9 bytes flags/props,
                              7bit-len+utf8 typename, 7bit-len+utf8 url

Two traps that cost days, both now handled:

 1. **The dictionary count is NOT at a fixed offset.** It shifts with the
    size of the KerningMap. We locate it by anchoring on the first entry,
    which is always the space glyph -> bytes `20 00 20 00 00 00`
    (key 0x20, NUL, then i32 Character == 0x20).

 2. **Entries are VARIABLE LENGTH.** The key is `UTF-8(codepoint) + 0x00`,
    so 1 byte for ASCII, 2 bytes for Latin-1, 3 bytes for CJK. A fixed
    38-byte stride parses fine up to U+007F and silently desynchronises
    after that. Always use the self-synchronising `walk_entries()`.

Atlas pixels live in a SEPARATE bundle record: font rec i -> texture rec i+1.
The font object only stores a *reference list* (URL); the Texture record
holds the real RGBA32 bitmap.
"""
from __future__ import annotations

import struct

from stride_bundle import rvint, group_offsets

GLYPH = 36


def walk_entries(od: bytes, start: int, limit: int = 60000):
    """Self-synchronising walk of CharacterToGlyph. Returns (entries, end).

    Each iteration reads the NUL-terminated UTF-8 key, then asserts that the
    following 36-byte glyph starts with an i32 Character equal to that key.
    That assertion is what makes the walk safe: one wrong step and it stops
    instead of drifting.
    """
    p = start
    entries = []
    while len(entries) < limit:
        try:
            q = od.index(b"\x00", p)
        except ValueError:
            break
        kb = od[p:q]
        if not (1 <= len(kb) <= 4):
            break
        try:
            key = ord(kb.decode("utf-8"))
        except Exception:
            break
        gs = q + 1
        if gs + GLYPH > len(od):
            break
        if struct.unpack_from("<i", od, gs)[0] != key:
            break
        entries.append(dict(
            key=key,
            keyraw=od[p:q + 1],
            glyph=od[gs:gs + GLYPH],
            sub=struct.unpack_from("<4i", od, gs + 4),
            off=struct.unpack_from("<2f", od, gs + 20),
            xa=struct.unpack_from("<f", od, gs + 28)[0],
            bi=struct.unpack_from("<i", od, gs + 32)[0],
        ))
        p = gs + GLYPH
    return entries, p


def _sane(entries) -> bool:
    return all(0 <= x["sub"][0] < 8192 and 0 <= x["sub"][1] < 8192 and
               0 <= x["sub"][2] <= 64 and 0 <= x["sub"][3] <= 64 for x in entries)


class StrideFont:
    """One OfflineRasterizedSpriteFont record inside a bundle."""

    def __init__(self, rec: int, idx):
        self.idx = idx
        self.rec = rec
        self.group = idx.content(rec)
        _, self.objoff, _ = group_offsets(self.group)
        self.od = self.group[self.objoff:]
        self._parse_header()
        self._parse_dict()
        self._parse_textures()

    # ---------------------------------------------------------------- header
    def _parse_header(self):
        od = self.od
        self.baseOffsetY = struct.unpack_from("<f", od, 1)[0]
        self.lineSpacing = struct.unpack_from("<f", od, 5)[0]

        p = 10
        self.kerningCount = struct.unpack_from("<i", od, p)[0]
        p += 4
        self.kerning = []
        for _ in range(max(0, min(self.kerningCount, 100000))):
            self.kerning.append((struct.unpack_from("<i", od, p)[0],
                                 struct.unpack_from("<f", od, p + 4)[0]))
            p += 8
        self.size_off = p
        self.size = struct.unpack_from("<f", od, p)[0]

        # Anchor on the space glyph: 20 00 20 00 00 00
        anchor = od.find(bytes([0x20, 0x00, 0x20, 0x00, 0x00, 0x00]))
        if anchor < 4:
            raise ValueError(f"rec {self.rec}: space-glyph anchor not found")
        self.dict_start = anchor
        self.count_off = anchor - 4
        self.count = struct.unpack_from("<i", od, self.count_off)[0]

    # ----------------------------------------------------------- dictionary
    def _parse_dict(self):
        entries, end = walk_entries(self.od, self.dict_start)
        if not entries or not _sane(entries):
            raise ValueError(
                f"rec {self.rec}: dict walk invalid at od[{self.dict_start}]")
        self.entries = entries
        self.dict_end = end
        self.count_matches = (self.count == len(entries))

    # -------------------------------------------------------------- textures
    def _parse_textures(self):
        od = self.od
        p = self.dict_end
        self.ntex = struct.unpack_from("<i", od, p)[0]
        p += 4
        self.textures = []
        for _ in range(self.ntex):
            flags = od[p:p + 9]
            p += 9
            tl, p = rvint(od, p)
            tname = od[p:p + tl].decode("utf-8")
            p += tl
            ul, p = rvint(od, p)
            url = od[p:p + ul].decode("utf-8")
            p += ul
            self.textures.append(dict(flags=flags, tname=tname, url=url))
        self.tail_end = p

    # ----------------------------------------------------------------- atlas
    def atlas_record(self) -> int:
        return self.rec + 1

    def atlas(self) -> dict:
        """Parse the paired Texture record -> dict(width, height, pixels, ...).

        Texture object data (empirically verified):
            [0]      bool isStreamable (== 0)
            [1:5]    "TKTX" magic
            [5:33]   ImageDescription, 7 x int32:
                       Dimension, Width, Height, Depth, ArraySize, MipLevels,
                       Format
            [33:37]  int32 totalSizeInBytes (== W*H*4 for R8G8A8_UNorm)
            [37:..]  RGBA32 pixels, followed by a few trailing bytes

        >>> ImageDescription is SEVEN int32, not eight. Parsing eight shifts
        >>> every pixel by 4 bytes and produces a plausible-looking but
        >>> completely wrong atlas.
        """
        i = self.atlas_record()
        c = self.idx.content(i)
        q = 8
        tl, q = rvint(c, q)
        tname = c[q:q + tl].decode("utf-8")
        q += tl
        off_obj = struct.unpack_from("<i", c, q)[0]
        q += 4
        q += 4                                   # OffsetToReferences
        obj = c[off_obj:]
        assert obj[0] == 0, "isStreamable flag should be 0"
        assert obj[1:5] == b"TKTX", f"bad image magic {obj[1:5]!r}"
        dim, w, h, depth, arr, mips, fmt = struct.unpack_from("<7i", obj, 5)
        total = struct.unpack_from("<i", obj, 33)[0]
        px = obj[37:37 + total]
        return dict(rec=i, width=w, height=h, dim=dim, depth=depth, array=arr,
                    mips=mips, format=fmt, total=total, pixels=px,
                    expected=w * h * 4, tname=tname)

    # ----------------------------------------------------------------- utils
    def summary(self) -> dict:
        cps = [e["key"] for e in self.entries]
        return dict(
            rec=self.rec,
            url=self.textures[0]["url"] if self.textures else "?",
            size=self.size,
            count=len(self.entries),
            declared_count=self.count,
            kerning=len(self.kerning),
            dict_start=self.dict_start,
            dict_end=self.dict_end,
            tail_end=self.tail_end,
            od_len=len(self.od),
            cp_min=min(cps), cp_max=max(cps),
            cjk=sum(1 for c in cps if 0x4E00 <= c <= 0x9FFF),
            need_w=max(e["sub"][0] + e["sub"][2] for e in self.entries),
            need_h=max(e["sub"][1] + e["sub"][3] for e in self.entries),
        )


def main():
    """python stride_font.py <bundle> <rec> [<rec> ...]"""
    import sys
    from stride_bundle import BundleIndex
    if len(sys.argv) < 3:
        print(__doc__)
        return
    idx = BundleIndex(sys.argv[1])
    print("sanity:", idx.sanity())
    for arg in sys.argv[2:]:
        f = StrideFont(int(arg), idx)
        s = f.summary()
        a = f.atlas()
        print(f"rec {s['rec']:5d} {s['url']:30s} size={s['size']:5.1f} "
              f"n={s['count']:4d} kern={s['kerning']:3d} "
              f"cp={s['cp_min']}..{s['cp_max']} CJK={s['cjk']:4d} "
              f"| atlas {a['width']}x{a['height']} fmt={a['format']} "
              f"px_ok={a['total'] == a['expected']} "
              f"| tail_clean={s['tail_end'] == s['od_len']}")


if __name__ == "__main__":
    main()
