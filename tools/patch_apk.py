#!/usr/bin/env python3
"""Build a de-telemetried PixelJoy APK for personal use.

    python3 tools/patch_apk.py PixelJoy-V1.4.1.apk                # privacy profile
    python3 tools/patch_apk.py PixelJoy-V1.4.1.apk --offline      # + no INTERNET permission

Needs: java (11+). apktool and uber-apk-signer are downloaded (pinned) into
~/.cache/pixeljoy-patch on first run. The output is signed with your own key
(created at ~/.cache/pixeljoy-patch/pixeljoy.keystore unless --keystore is
given). Android refuses to update a Play-signed install with it, so uninstall
the original app first (this wipes the app's local data).

What gets patched, and why, is described in docs/APP_PRIVACY.md.
The vendor APK and the patched output are not to be committed (see .gitignore).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

CACHE = Path(os.environ.get("PIXELJOY_PATCH_CACHE", Path.home() / ".cache" / "pixeljoy-patch"))
TOOLS = {
    "apktool.jar": "https://github.com/iBotPeaches/Apktool/releases/download/v2.10.0/apktool_2.10.0.jar",
    "uber-apk-signer.jar": "https://github.com/patrickfav/uber-apk-signer/releases/download/v1.3.0/uber-apk-signer-1.3.0.jar",
}
KNOWN_APK_SHA256 = {"adc27db0311e083ede308f7dadc83a745d3bcbbedbf96a625aa12ff6960628e1": "1.4.1"}

VOID = ["    .locals 0", "    return-void"]
FALSE = ["    .locals 1", "    const/4 v0, 0x0", "    return v0"]
NULL_ID = ["    .locals 1", '    const-string v0, "00000000000000000000000000000000"', "    return-object v0"]

# (smali class path, exact .method line, replacement body, reason)
PATCHES = [
    ("com/th/common/utils/SysUtils", ".method public final getDeviceID()Ljava/lang/String;", NULL_ID,
     "appUUID header = Widevine MediaDrm deviceUniqueId (hardware ID, survives factory reset), sent on every API call"),
    ("com/th/common/http/request/UUIDRequest", ".method public final getRealIp()V", VOID,
     "startup IP-geolocation lookup (model/multi/app/getDeviceIp)"),
    ("com/th/common/utils/AppUtils", ".method public final checkAppVersion()V", VOID,
     "startup update check (public/app/update/checkUpdate)"),
    ("com/th/common/utils/PgPerUtil", ".method public final checkDevVersion()V", VOID,
     "pgyer.com beta-distribution check (dev builds)"),
    ("com/th/pixeljoy/utlis/UserRequest", ".method public final clickCommunity(Landroidx/lifecycle/LifecycleOwner;I)V", VOID,
     "community post click tracking (app/community/post/click)"),
    ("com/th/common/utils/AppUtils", ".method public final isSupportIntelligentVoice()Z", FALSE,
     "hide the voice assistant UI"),
    ("com/th/common/vosk/AAIManager", ".method public final init()V", VOID,
     "Tencent cloud ASR client init (asr.cloud.tencent.com) at app start"),
    ("com/th/common/vosk/AAIManager", ".method public final startAudioRecognize()V", VOID,
     "never stream microphone audio to Tencent cloud ASR"),
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def tool(name: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / name
    if not p.exists():
        print(f"[*] downloading {name}")
        urllib.request.urlretrieve(TOOLS[name], p)
    return p


def run(*cmd: str) -> None:
    print("[$]", " ".join(cmd))
    subprocess.run(cmd, check=True)


def find_smali(root: Path, cls: str) -> Path:
    hits = list(root.glob(f"smali*/{cls}.smali"))
    if len(hits) != 1:
        raise SystemExit(f"[!] {cls}.smali: expected 1 match, found {len(hits)} (different app version?)")
    return hits[0]


def stub_method(path: Path, header: str, body: list[str]) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == header)
    except StopIteration:
        raise SystemExit(f"[!] method not found in {path.name}: {header} (different app version?)")
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == ".end method")
    lines[start + 1 : end] = body
    path.write_text("\n".join(lines), encoding="utf-8")


def strip_internet(manifest: Path) -> None:
    text = manifest.read_text(encoding="utf-8")
    new, n = re.subn(r'\s*<uses-permission android:name="android\.permission\.INTERNET"\s*/>', "", text)
    if n != 1:
        raise SystemExit("[!] INTERNET permission not found exactly once in manifest")
    manifest.write_text(new, encoding="utf-8")


def ensure_keystore(ks: Path, alias: str, password: str) -> None:
    if ks.exists():
        return
    print(f"[*] creating signing key {ks}")
    run("keytool", "-genkeypair", "-keystore", str(ks), "-storepass", password, "-keypass", password,
        "-alias", alias, "-keyalg", "RSA", "-keysize", "3072", "-validity", "36500",
        "-dname", "CN=pixeljoy-local")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("apk", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--offline", action="store_true", help="also remove the INTERNET permission")
    ap.add_argument("--keystore", type=Path, default=CACHE / "pixeljoy.keystore")
    ap.add_argument("--alias", default="pixeljoy")
    ap.add_argument("--password", default=os.environ.get("PIXELJOY_KS_PASS", "pixeljoy-local"))
    ap.add_argument("--keep-workdir", action="store_true")
    a = ap.parse_args()

    digest = sha256(a.apk)
    ver = KNOWN_APK_SHA256.get(digest)
    print(f"[*] input {a.apk} sha256={digest} ({'v' + ver if ver else 'UNKNOWN version - patches may not apply'})")
    profile = "offline" if a.offline else "privacy"
    out = a.out or a.apk.with_name(f"{a.apk.stem}-{profile}.apk")

    apktool, signer = tool("apktool.jar"), tool("uber-apk-signer.jar")
    work = Path(tempfile.mkdtemp(prefix="pixeljoy-patch-"))
    try:
        dec = work / "dec"
        run("java", "-jar", str(apktool), "d", "-f", "-o", str(dec), str(a.apk))
        for cls, header, body, why in PATCHES:
            stub_method(find_smali(dec, cls), header, body)
            print(f"[+] {cls.split('/')[-1]}.{header.split()[-1].split('(')[0]}: {why}")
        if a.offline:
            strip_internet(dec / "AndroidManifest.xml")
            print("[+] removed android.permission.INTERNET")
        unsigned = work / "unsigned.apk"
        run("java", "-jar", str(apktool), "b", "-o", str(unsigned), str(dec))
        a.keystore.parent.mkdir(parents=True, exist_ok=True)
        ensure_keystore(a.keystore, a.alias, a.password)
        signed_dir = work / "signed"
        run("java", "-jar", str(signer), "-a", str(unsigned), "-o", str(signed_dir),
            "--ks", str(a.keystore), "--ksAlias", a.alias, "--ksPass", a.password, "--ksKeyPass", a.password)
        produced = next(signed_dir.glob("*.apk"))
        shutil.move(produced, out)
        print(f"[✓] wrote {out}  sha256={sha256(out)}")
        print("    install: adb uninstall com.th.pixeljoy && adb install " + str(out))
    finally:
        if a.keep_workdir:
            print(f"[*] workdir kept: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
