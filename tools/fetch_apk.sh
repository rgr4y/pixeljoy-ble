#!/usr/bin/env bash
# Re-download the vendor APK and decompile it with jadx, for re-checking the protocol.
# Output goes to ./re/ (ignored by git). The APK and decompiled sources are not committed.
set -euo pipefail

OUT="${1:-re}"
APK_URL="https://pixeljoyapk.smartthingsapp.com/APK/PixelJoy-V1.4.1.apk"   # from https://pixeljoy.smartthingsapp.com/app/QRcode
APK_SHA256="adc27db0311e083ede308f7dadc83a745d3bcbbedbf96a625aa12ff6960628e1"
JADX_VER="1.5.1"

mkdir -p "$OUT"
cd "$OUT"

[ -f PixelJoy.apk ] || curl -fL -o PixelJoy.apk "$APK_URL"
echo "$APK_SHA256  PixelJoy.apk" | sha256sum -c - || echo "WARNING: APK hash differs (new version?) - offsets in docs may not match"

if [ ! -x jadx/bin/jadx ]; then
  curl -fL -o jadx.zip "https://github.com/skylot/jadx/releases/download/v${JADX_VER}/jadx-${JADX_VER}.zip"
  unzip -q -o jadx.zip -d jadx
fi

JAVA_OPTS="-Xmx6g" jadx/bin/jadx -d src --no-res -j 8 --show-bad-code PixelJoy.apk || true
unzip -o -q PixelJoy.apk 'assets/th_dev_cfg.json' 'assets/deviceSize.json' -d assets_raw

echo
echo "Protocol code:  $OUT/src/sources/com/th/common/protocol/"
echo "BLE plumbing:   $OUT/src/sources/com/th/common/utils/BleConnectUtil.java"
echo "Model table:    $OUT/assets_raw/assets/th_dev_cfg.json"
