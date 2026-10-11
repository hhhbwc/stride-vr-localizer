#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rasterise new glyphs into an existing font atlas (shelf packing).

Design rules learned the hard way (all of them are load-bearing):

 1. **Never move the original pixels.** The Latin glyphs already in the atlas
    keep their Subrects; CJK glyphs are packed strictly *below* the original
    canvas, starting at `start_y = original_height + 1`. The atlas only ever
    grows downwards, so nothing that already works can break.

 2. **Offset.y is relative to the baseline and NEGATIVE upwards.** PIL gives
    you a positive top-coordinate; the engine wants `pil_y - ascent`. Getting
    this backwards puts every CJK glyph ~2 lines too low.

 3. **Keep the original trailing bytes of the Texture object.** PICO builds
    append 4 zero bytes after the pixel array, Quest builds append 8. Copy
    whatever was there; never synthesise it.

 4. **The atlas width stays, the height grows.** A 256x212 atlas becomes
    256x1235 for ~600 glyphs. That is fine - the font object is free to grow.

The rasteriser is Pillow; bring any TTF/OTF you are allowed to redistribute
(Noto Sans SC / Noto Sans JP / Noto Naskh Arabic ... all OFL).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

from stride_font import GLYPH, StrideFont

PAD = 1          # 1px transparent border so glyphs never bleed into each other
CANVAS_PAD = 8   # slack around the rasterisation canvas


@dataclass
class RepackResult:
    rec: int
    weight: str
    old_size: tuple[int, int]
    new_h: int
    start_y: int
    entries: list = field(default_factory=list)
    atlas: bytes = b""


def rasterize(pil_font, cp: int):
    """Render one codepoint on a scratch canvas and crop to its ink box."""
    canvas = 4 * max(pil_font.size, 1) + 64
    img = Image.new("L", (canvas, canvas), 0)
    ImageDraw.Draw(img).text((CANVAS_PAD, CANVAS_PAD), chr(cp),
                             font=pil_font, fill=255)
    bbox = img.getbbox()
    if bbox is None:                      # whitespace-only glyph
        return None
    try:
        advance = pil_font.getlength(chr(cp))
    except Exception:
        advance = bbox[2] - bbox[0]
    return img.crop(bbox), bbox[0] - CANVAS_PAD, bbox[1] - CANVAS_PAD, advance


def shelf_pack(items, atlas_w: int, start_y: int, max_h: int = 1 << 16):
    """Classic shelf (row) packing. Returns (placements, used_height)."""
    placements = []
    x, y, shelf_h = 0, start_y, 0
    for key, w, h in items:
        cw, ch = w + PAD, h + PAD
        if x + cw > atlas_w:
            x = 0
            y += shelf_h
            shelf_h = 0
        if y + ch > max_h:
            return None, None
        placements.append((key, x, y, w, h))
        x += cw
        shelf_h = max(shelf_h, ch)
    return placements, y + shelf_h


def build(idx, rec: int, ttf_path: str, codepoints, weight: str = "regular",
          verbose: bool = True) -> RepackResult:
    """Produce new glyph entries + a grown atlas for font record `rec`."""
    font = StrideFont(rec, idx)
    atlas = font.atlas()
    assert atlas["total"] == atlas["expected"], f"rec{rec}: atlas size field corrupt"
    # The dictionary start is located by anchoring on the space glyph, so it
    # is version-dependent (32 on VD 1.34.22.0). Do not hard-code it.
    assert font.dict_start > 14, f"rec{rec}: implausible dict_start={font.dict_start}"
    assert font.count_matches, (
        f"rec{rec}: declared dict count {font.count} != walked {len(font.entries)}")

    W, H = atlas["width"], atlas["height"]
    old_px = atlas["pixels"]
    have = {e["key"] for e in font.entries}
    todo = [cp for cp in codepoints if cp not in have]

    pil = ImageFont.truetype(ttf_path, int(round(font.size)))
    ascent, _ = pil.getmetrics()

    start_y = H + 1
    rendered = []
    for cp in todo:
        r = rasterize(pil, cp)
        if r:
            rendered.append((cp,) + r)

    placements, used = shelf_pack(
        [(cp, bmp.width, bmp.height) for cp, bmp, _, _, _ in rendered],
        W, start_y)
    assert placements, f"rec{rec}: packing failed (atlas width {W} too small?)"

    new_h = max(H, used)
    if verbose:
        print(f"  rec{rec} ({weight}): atlas {W}x{H} -> {W}x{new_h}, "
              f"start_y={start_y}, +{len(placements)} glyphs")

    new_px = bytearray(W * new_h * 4)
    for row in range(H):                       # copy original rows verbatim
        new_px[row * W * 4:(row + 1) * W * 4] = old_px[row * W * 4:(row + 1) * W * 4]

    out_entries = []
    for (cp, x, y, gw, gh), (_cp, bmp, xo, yo, adv) in zip(placements, rendered):
        assert cp == _cp
        signed_yo = yo - ascent                # trap #2 above
        for yy in range(gh):
            for xx in range(gw):
                v = bmp.getpixel((xx, yy))
                if v:
                    o = ((y + yy) * W + (x + xx)) * 4
                    new_px[o] = new_px[o + 1] = new_px[o + 2] = new_px[o + 3] = v
        g = bytearray(GLYPH)
        struct.pack_into("<i", g, 0, cp)                       # Character
        struct.pack_into("<4i", g, 4, x, y, gw, gh)            # Subrect
        struct.pack_into("<2f", g, 20, float(xo), float(signed_yo))  # Offset
        struct.pack_into("<f", g, 28, float(adv))              # XAdvance
        struct.pack_into("<i", g, 32, 0)                       # BitmapIndex
        out_entries.append(dict(key=cp, sub=[x, y, gw, gh],
                                off=[float(xo), float(signed_yo)],
                                xa=float(adv), glyph=bytes(g)))

    return RepackResult(rec=rec, weight=weight, old_size=(W, H), new_h=new_h,
                        start_y=start_y, entries=out_entries, atlas=bytes(new_px))
