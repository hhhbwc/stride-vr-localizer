#!/usr/bin/env bash
# Install a freshly built APK on the headset and check that it survives.
#
#   tools/devtest.sh <local.apk> [tag] [--config config.json]
#
# What it does (each step exists because of a specific failure mode):
#   1. force-stop the app
#   2. push the APK to /data/local/tmp
#   3. root-copy it over the installed base.apk  (NOT `adb install` - a
#      re-signed APK is rejected by the licence check)
#   4. WAKE THE SCREEN - with the display off the app never initialises the
#      font system, so "the process is alive" proves nothing
#   5. launch, then poll pidof up to 60 s
#   6. dump the crash signatures from logcat
#
# Exit code 0 = still alive after 60 s.
set -u

LOCAL="$1"
TAG="${2:-test}"
CONFIG="${3:-config.json}"
[ "${4:-}" = "--config" ] && CONFIG="${5:-config.json}"

PY="${VDL_PYTHON:-python}"
eval "$($PY - "$CONFIG" <<'EOF'
import json, sys, shlex
cfg_path = sys.argv[1]
try:
    cfg = json.load(open(cfg_path, encoding='utf-8'))
except Exception:
    cfg = {}
d = cfg.get('device', {})
print('SER=' + shlex.quote(d.get('serial', '')))
print('PKG=' + shlex.quote(d.get('package', 'VirtualDesktop.Android')))
print('APPAPK=' + shlex.quote(d.get('installed_apk', '')))
EOF
)"

[ -n "$SER" ] && A="adb -s $SER" || A="adb"
[ -n "$APPAPK" ] || { echo "device.installed_apk is not set in $CONFIG"; exit 1; }

TARGET="$APPAPK"
echo "[$TAG] package=$PKG target=$TARGET"

echo "[$TAG] force-stop"
$A shell "am force-stop $PKG" >/dev/null 2>&1
sleep 1

echo "[$TAG] push $LOCAL"
$A push "$LOCAL" "/data/local/tmp/$TAG.apk" 2>&1 | tail -1

echo "[$TAG] install (root copy)"
OK=0
for i in 1 2 3 4 5 6; do
  for SU in "su -c" "su -mm -c"; do
    OUT=$( $A shell "$SU 'cp /data/local/tmp/$TAG.apk $TARGET && chmod 644 $TARGET && chown system:system $TARGET && restorecon $TARGET && ls -la $TARGET'" 2>&1 | tr -d '\r' )
    if echo "$OUT" | grep -qE '^-rw.*base\.apk$'; then
      OK=1
      echo "[$TAG] installed (try $i, $SU): $(echo "$OUT" | grep -E '^-rw.*base\.apk$')"
      break 2
    fi
  done
  sleep 1
done
if [ "$OK" != "1" ]; then
  echo "[$TAG] FAILED to install. Last output: $OUT"
  exit 1
fi

echo "[$TAG] wake screen"
$A shell "input keyevent KEYCODE_WAKEUP" >/dev/null 2>&1
$A shell "wm dismiss-keyguard" >/dev/null 2>&1
sleep 2

echo "[$TAG] launch"
$A logcat -c >/dev/null 2>&1
$A shell "monkey -p $PKG -c android.intent.category.LAUNCHER 1" >/dev/null 2>&1

ALIVE=0
for t in 5 10 15 20 25 30 40 50 60; do
  sleep 5
  PID=$( $A shell "pidof $PKG" | tr -d '\r' )
  if [ -n "$PID" ]; then
    ALIVE=1; echo "[$TAG] t=${t}s alive pid=$PID"
  else
    echo "[$TAG] t=${t}s dead"; break
  fi
done

echo "[$TAG] logcat signatures"
$A logcat -d 2>/dev/null | grep -E "VRD|FATAL|NullReference|SIGSEGV|FontSystem|Xenko" | head -15

if [ "$ALIVE" = "1" ]; then
  echo "[$TAG] SURVIVED 60s (backup kept at /data/local/tmp/$TAG.apk)"
else
  echo "[$TAG] CRASHED - restore your last known-good APK from /data/local/tmp/"
fi
[ "$ALIVE" = "1" ]
