# APK packaging

Replacing a ~1 GB STORED bundle inside an APK without breaking the archive —
and without re-signing it.

## 1. Why not just re-zip

Virtual Desktop validates its licence. A re-signed APK is rejected, so the
patch has to keep the original signature.

## 2. What the surgery does

`tools/apksurgery.py` rebuilds the APK entry-by-entry while preserving:

* every unmodified payload byte,
* every original local file header (only CRC / csize / usize of the target
  change),
* the APK Signing Block **verbatim**,
* the v1 `META-INF` entries,
* the central directory structure (re-serialised with the new offsets).

The target entry stays STORED: the new bundle bytes are written directly, not
deflated.

## 3. Two passes, in this order

```
pass 1:  data bundle    -> APK   (payloads + data record table)
pass 2:  index bundle   -> APK   (index record table, patched after the move)
```

Both bundles are entries in the same APK, and the data bundle's size changes
between the two passes. Patching the data bundle first means the second pass
operates on an APK whose central directory already knows the new offsets.

If you only patch the data bundle, the app crashes (the index table still
points at the old layout — [07 — Pitfalls #1](07-pitfalls.md)). If you only
patch the index bundle, nothing changes at all.

## 4. Install

Root file replacement, never `adb install`:

```bash
adb -s <serial> push work/my-final.apk /data/local/tmp/my.apk
adb -s <serial> shell "su -c 'cp /data/local/tmp/my.apk <installed_apk> && \
    chmod 644 <installed_apk> && chown system:system <installed_apk> && \
    restorecon <installed_apk>'"
```

The copied signing block carries stale content digests. The platform does not
re-hash on launch, so root replacement works; the normal installer would
reject it.

## 5. Verification built into the tool

After writing, `apksurgery.py` re-opens the output with Python's `zipfile`,
runs `testzip()` (CRC + inflate check over every entry), and reads the target
entry back through the ZIP reader to confirm it is byte-identical to the
replacement file. It then prints the output SHA-256.
