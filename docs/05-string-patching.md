# String patching

The one rule, the proof, and the tool.

## 1. How strings are stored

Serialised UI strings are `7-bit varint(length) + UTF-8 bytes`:

```
\x0bCOMPUTERS                  <- varint(9) + "COMPUTERS"
\x0bEnvironment                <- varint(11) + "Environment"
\x0bitemEnvironment            <- varint(16) + "itemEnvironment"   (control name)
```

## 2. The rule

**Replace the whole `varint + text` pattern. Never the bare text.**

```python
pattern     = wr_vint(len(en_bytes)) + en_bytes
replacement = wr_vint(len(tr_bytes)) + tr_bytes
group       = group.replace(pattern, replacement)
```

The length prefix is what tells a label from a control name that happens to
contain the same letters. `Environment` → `btnSettings`-style corruption is
impossible this way, because the varint values differ.

## 3. What is *not* the problem: length

An earlier iteration of this project concluded that UIPage strings must be
replaced **at equal byte length**. That was wrong. Length changes are fine,
provided the substitution is exact — verified by a build where record 954 went
from 130,209 to 130,198 bytes and the app ran normally.

The builds that crashed changed length *and* used a bare `bytes.replace`, and
the bare replace is what killed them:

```
"Environment" -> "环境"   also rewrites:
    itemEnvironment            (control name)
    itemEnvironmentDLC         (control name)
    UI/ControlPanel/EnvironmentSheet   (asset URL)
=> control lookup fails => ControlPanel.UnloadContent() NRE => SIGSEGV
```

`string_patcher.py` still offers `equal` and `padded` modes so you can
reproduce the failure yourself; `tests/smoke_test.py` has a test named
`test_padded_mode_is_unsafe_by_design` that asserts the control name is
destroyed. Default is `strict`.

## 4. Which records hold UI text

Only the `UIPage` objects. On VD 1.34.22.0 there are eight:

| record | content |
|---|---|
| 869 | performance overlay |
| 954 | main control panel (the big one) |
| 1211 | video toolbar (timecodes, nothing to translate) |
| 1405 | notifications |
| 1506 | virtual keyboard |
| 1928 | UI library / connection hints |
| 1963 | confirmation dialog |
| 1969 | video format (SBS) |

Do not go hunting through shaders or textures. `tools/probe.py` lists the
types for you.

## 5. Ordering

Substitute **longest source strings first**. Otherwise `Version` can eat text
that belongs to `Version: 1.0.0`. `load_pairs()` returns the table already
sorted by source byte length, descending.

## 6. What NOT to translate

* control names (`btnSettings`, `gridGames`, `tbResolution`, `itemEnvironment`)
* asset URLs / paths (`UI/ControlPanel/EnvironmentSheet`)
* anything that is an identifier in code

If `dump_strings.py` surfaced it and it looks like an identifier, delete the
row.

## 7. Field mode — for labels that carry padding

Some engines store a label as `varint(N) + " Release trigger to complete "`,
i.e. the text is padded *inside* its field. Plain strict mode cannot see those,
because the varint no longer equals the text length.

`--mode strict-field` adds a second, equally safe pass: it walks every
length-prefixed field in the group, and rewrites a field only when its
**stripped** body equals the source string. The field length is preserved, so
not even the varint changes.

It is safe for the same reason strict mode is: the match is anchored at a
length prefix, so `itemEnvironment` (whose prefix is 16, not 11) can never be
hit by the rule for `Environment`.

Measured on the reference build: strict catches 40 strings / 50 occurrences in
record 954, strict-field catches exactly the same 40 / 50. The residual English
in that record turned out to be *control-name substrings*, not padded labels —
so field mode adds nothing there. It is kept because other app versions and
other engines do pad their labels, and the cost of the extra pass is one linear
scan.

## 8. Residual English after a build

Expect some. Control names legitimately contain English words
(`tbFramerate`, `itemEnvironment`, `GameSuperResolution`), and a few labels are
longer than their translations can be made to fit under a chosen equal-length
strategy. The residual report in `build.py` output lists occurrences; check them
by eye rather than chasing the count to zero.

On the reference build, the 98 residual occurrences in record 954 contained
**zero** untranslated labels once the control-name substrings were accounted
for — which is the expected outcome, not a bug.
