# Device testing

## 1. Install: root file replacement, never `adb install`

```bash
adb -s <serial> shell "su -c 'cp /data/local/tmp/<tag>.apk <installed_apk> && \
    chmod 644 <installed_apk> && chown system:system <installed_apk> && \
    restorecon <installed_apk>'"
```

`<installed_apk>` is the app's own `base.apk`, e.g.
`/data/app/VirtualDesktop.Android-v2Wc6C-HdWl13uP7n5UXjg==/base.apk`. Find it
with `adb shell pm path <package>`.

`tools/devtest.sh` automates the whole sequence. Root may need a few tries —
alternate `su -c` and `su -mm -c`.

## 2. Wake the screen first

With the display off the app never initialises its font system, so a process
that "survives 60 seconds" proves nothing.

```bash
adb shell input keyevent KEYCODE_WAKEUP
adb shell wm dismiss-keyguard
```

## 3. Liveness and crash signatures

```bash
adb shell monkey -p <package> -c android.intent.category.LAUNCHER 1
adb shell pidof <package>          # repeat for 60 s
adb logcat -d | grep -E "VRD|FATAL|NullReference|SIGSEGV|FontSystem|Xenko"
```

The signature of a failed bundle/font load:

```
E VRD  : System.NullReferenceException
          at Xenko.Graphics.Font.FontSystem.Unload()
          at Xenko.Rendering.Fonts.GameFontSystem.UnloadContent()
F libc : Fatal signal 11 (SIGSEGV) ... (Game Thread)
```

and the signature of a corrupted control name:

```
E VRD : System.NullReferenceException
   at VirtualDesktop.Mobile.ControlPanel.UnloadContent()
   ...
F libc : Fatal signal 11 (SIGSEGV), fault addr 0x0 in tid (Game Thread)
```

Death within 15 s = load-time failure. Surviving a minute = the build is
basically healthy.

## 4. Screenshots cannot see VR

`adb shell screencap` captures the system layer only — under a VR app you get
the system dialog, never the app. Judge rendering by liveness + logcat, then
have a human look through the headset.

## 5. Keep a rollback

Copy the current APK to `/data/local/tmp/<tag>.apk` before every experiment.
That is the only practical way back from a boot-loop-free but broken app.
