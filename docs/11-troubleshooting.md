# Troubleshooting and FAQ

Real symptoms, in the order you are likely to meet them.

---

## Crashes

### App dies ~15 s after launch, `FontSystem.Unload` NRE + SIGSEGV

```
E VRD  : System.NullReferenceException
          at Xenko.Graphics.Font.FontSystem.Unload()
F libc : Fatal signal 11 (SIGSEGV) ... (Game Thread)
```

Meaning: **content failed to load** — any content, not necessarily fonts. The
engine swallows the original exception and this is the debris.

| check | how |
|---|---|
| both record tables patched? | re-run `build.py`; it asserts the two tables agree before writing |
| `size_nc` consistent? | `build.py` prints `table_mismatch=0 discontinuities=0`; anything else is fatal |
| atlas pixels consistent? | `pixels_ok=True` in the read-back section |
| did you patch the APK twice? | data bundle first, index bundle second ([09](09-apk-packaging.md)) |

Bisect with `--pages none` (fonts only) and `--pages 954` (one page) to see
which half breaks.

### App dies with `ControlPanel.UnloadContent` NRE

```
E VRD : System.NullReferenceException
   at VirtualDesktop.Mobile.ControlPanel.UnloadContent()
F libc : Fatal signal 11 (SIGSEGV), fault addr 0x0 in tid (Game Thread)
```

Meaning: a **control name or asset URL was rewritten**. You used a bare text
replace somewhere. Switch to `--mode strict` (or `strict-field`) and re-check
your TSV for rows whose source is an identifier
([05 §6](05-string-patching.md)).

### App dies only when the screen is off

Not a bug. With the display off the font system is never initialised, so
liveness proves nothing. Wake the device first ([06](06-device-testing.md)).

### Nothing crashes, but the new glyphs are blank

* The glyph is not in the dictionary → check `all_codepoints()` covered it
  (punctuation is the usual omission).
* The atlas grew but the Texture object's `totalSizeInBytes` did not →
  `pixels_ok=False` in the read-back.
* The atlas grew downwards past a limit the renderer imposes → keep an eye on
  atlas height; 256×1235 worked, but there is no documented ceiling.

### Glyphs render two lines too low

`Offset.y` sign. It is `pil_y - ascent`, negative upwards
([04 §3](04-atlas-repacking.md)).

### Glyphs render as garbage / shifted noise

`ImageDescription` was parsed as eight int32. It is seven
([03 §3](03-font-format.md)).

---

## "It works on my build but not on theirs"

| symptom | cause |
|---|---|
| fonts are at different record numbers | app version differs → `tools/probe.py` |
| `apk.data_entry` not found | the GUID in the bundle filename is version-specific → read it off the APK with `tools/extract.py`'s listing |
| UI pages at different records | same → `tools/probe.py` |
| `default.bundle` (index) missing | some builds ship it elsewhere; find the 224 KB `.bundle` |

---

## FAQ

**Can I use this on a game instead of an app?**
Only if it uses the same Stride/Xenko serialisation. The bundle format and the
`KNHC` chunk header are engine-level, so any Stride game with a `.bundle` data
directory is a candidate. The UI page records and font records differ per game;
`tools/probe.py` finds them.

**Can I translate text that is rendered in 3D (world-space text)?**
Not with this toolchain — it only patches the 2D UI page objects. World-space
text is drawn through the same font system though, so once a font has the
glyphs, in-world text in your script will render.

**Do I need root?**
For installing, yes (root file replacement). For developing the translation and
inspecting bundles, no — `extract.py`, `probe.py`, `dump_strings.py` and
`build.py` are all offline.

**Why two `apksurgery.py` passes?**
The data bundle and the index bundle are both entries in the same APK, and the
first pass changes the data bundle's size, which moves the offsets the second
pass must then follow. See [09](09-apk-packaging.md).

**My translation is longer than the English. Does it fit?**
With `strict` mode, yes — any length works. With `field` mode, the target must
fit inside the original field length (the excess is skipped with a warning).
With `equal`/`padded`, do not use those modes at all.

**A label is still in English after the build.**
Look at whether it is a *label* or a *substring of a control name*.
`grep` the residual list: `tbFramerate`, `itemEnvironment`,
`GameSuperResolution` are control names and are supposed to stay English.
If it is a genuine label, its source string in your TSV probably differs from
the one in the file by whitespace — use `--mode strict-field`.

**Can I contribute a language?**
Yes: `locales/<lang>/strings.tsv` + `locales/<lang>/fonts.json`. See
[08-new-language.md](08-new-language.md).

**Is the font you used redistributable?**
The reference build used a font shipped with the headset. Nothing from it is in
this repository. Bring your own — Noto covers essentially every script and is
OFL-licensed.

**What if my script needs complex shaping (Thai, Indic)?**
Rasterising individual codepoints will not produce correct text. You need a
shaping step before rasterisation; Pillow built against libraqm provides one.

**How do I know my build is actually good?**
`build.py` prints, and you should check, all of:

```
verify: table_mismatch=0 discontinuities=0 decompressible=2074/2120 tail_ok=True
recNNN: decoded <N> expected <N> identical=True      (one per changed record)
recNNN: glyphs=619 cjk=375 atlas=256x1235 pixels_ok=True out_of_bounds=0
```

`decompressible` counts records whose chunk stream decodes; a handful of
records are undecodable **in the stock bundle too**, so the baseline number
matters more than the absolute one. Then the only real test is on the device
([06](06-device-testing.md)).
