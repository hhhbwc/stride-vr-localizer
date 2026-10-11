#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extract the two bundles from a pristine APK into work/.

    python tools/extract.py [--config config.json] [--apk path/to/base.apk]

Both entries are STORED inside the APK, so this is a plain byte-range copy -
no recompression, no re-signing, nothing that can break the archive.
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vdl_config import Config


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--apk", default=None, help="override apk.source")
    ap.add_argument("--set", action="append", default=[], metavar="a.b=value")
    args = ap.parse_args()

    cfg = Config.load(args.config, args.set)
    apk = args.apk or cfg.path("apk.source")
    if not os.path.exists(apk):
        print(f"APK not found: {apk}")
        print("Put your own copy of the APK there (see docs/01-getting-started.md).")
        return 1

    out_data = cfg.path("bundles.data")
    out_index = cfg.path("bundles.index")
    os.makedirs(os.path.dirname(out_data), exist_ok=True)

    with zipfile.ZipFile(apk) as zf:
        names = zf.namelist()
        entries = [n for n in names if n.endswith(".bundle")]
        print(f"APK entries: {len(names)}; bundles found: {len(entries)}")
        for n in entries:
            info = zf.getinfo(n)
            print(f"  {n}  ({info.file_size} bytes, "
                  f"method={'STORED' if info.compress_type == 0 else 'DEFLATE'})")

        data_entry = cfg.get("apk.data_entry")
        index_entry = cfg.get("apk.index_entry")

        for entry, dest in ((data_entry, out_data), (index_entry, out_index)):
            if entry not in names:
                print(f"\n!! entry not found: {entry}")
                print("   available bundles listed above - fix apk.data_entry / "
                      "apk.index_entry in your config.")
                return 1
            with zf.open(entry) as src, open(dest, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
            print(f"extracted -> {dest} ({os.path.getsize(dest)} bytes)")

    # quick structural report
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from stride_bundle import BundleIndex
    idx = BundleIndex(out_data, cfg.get("table.data_header", 28))
    print("\ndata bundle:", idx.sanity())
    print("next step: python tools/probe.py --config config.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
