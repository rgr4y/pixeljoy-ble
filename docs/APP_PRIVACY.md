# What the PixelJoy app sends home, and the personal-use patch

This audit is based on static analysis of PixelJoy v1.4.1 (`com.th.pixeljoy`,
sha256 `adc27db0…28e1`). [`tools/patch_apk.py`](../tools/patch_apk.py) rebuilds
the vendor APK without the items marked **patched**. The tool is for personal
use: don't redistribute the vendor APK or the patched build.

## What's not in it

No third-party analytics, ad or crash-reporting SDK (no Firebase, Umeng,
Bugly, AppsFlyer, Facebook, etc.). The manifest registers only androidx
startup and profile-installer components.

## Network endpoints

Base API: `https://pixeljoyapi.smartthingsapp.com/`, via EasyHttp/OkHttp.

**Every** request carries these headers (set in `k0.a`, the request interceptor):

| Header | Value |
|---|---|
| `appUUID` | **Widevine `MediaDrm` deviceUniqueId**. It's a hardware ID that survives factory reset and is the same across all apps that read it. **patched**: now 32 zeros |
| `appVersion`, `systemModel` ("Android"), `appLanguage` | harmless; left alone |

| Call | When | Patch |
|---|---|---|
| `model/multi/app/getDeviceIp` (IP geolocation, result kept as "location") | every app start | **patched** (no-op) |
| `public/app/update/checkUpdate` | every app start | **patched** (no-op) |
| `https://www.pgyer.com/apiv2/app/check` (Chinese beta-distribution service) | app start, dev builds | **patched** (no-op) |
| `app/community/post/click` (which community post you opened) | on tap | **patched** (no-op) |
| `asr.cloud.tencent.com` (Tencent Cloud speech-to-text) | voice feature: client init at app start; **mic audio streamed** after the wake word | **patched**: init and recognize are no-ops, and the voice UI is hidden |
| `devicemold/multi/app/getAppDevice` (model table), device-size list | app start | kept (falls back to the bundled table) |
| gallery / clock / community browse (`model/multi/app/*`, `app/community/*`), S3 images | when browsing | kept |
| OTA firmware (`public/app/ota/*`, `softscreenbucket` S3 zips) | device info / update | kept |
| email account (`system/emailUserApp/*`, captcha mail) | only if you sign in | kept (just don't sign in) |
| `public/app/user/setUserDevice` | dead code, never called | n/a |

### The voice feature

Enabling voice wake-up starts a foreground microphone service. Offline Vosk
models listen for **"Show Text"** or **"Show Animation"**. After the wake
word, your speech is sent to **Tencent Cloud ASR**, and the transcript is
displayed on the panel as text, or picked as an animation name. It's meant
for drivers who want to dictate rear-window messages. The patch disables the
cloud part, which makes the feature useless, so its UI is hidden as well.

## Profiles

* **privacy** (default): the patches above. Gallery, community browsing and
  OTA still work. Requests still reveal your IP address to the vendor's server
  whenever you use those features.
* **offline** (`--offline`): also removes `android.permission.INTERNET`. The
  OS then blocks every network call, which is the strongest guarantee.
  BLE control, text, DIY images and animations, clock, timers and slideshow
  all work, using the bundled model table. The online gallery, community
  feed and OTA updates don't.

## Install notes

* The rebuilt APK is signed with **your own key**. Android won't install it
  over the Play/vendor-signed app, so `adb uninstall com.th.pixeljoy` first.
  That wipes the app's local data (saved texts, DIY images, device names).
* Keep `~/.cache/pixeljoy-patch/pixeljoy.keystore`. You'll need it to install
  a future patched version without uninstalling again.
* If a new vendor version moves things around, the script stops with
  "method not found" instead of producing a half-patched build.
