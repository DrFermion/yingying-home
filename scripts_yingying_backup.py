#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
荧荧每周日自动备份脚本
========================
1. hermes backup 打包整个 Hermes 配置 (config/.env/memories/skills/cron/scripts/sessions)
2. 打包荧荧外部资产 (E:\\yingying-home 桌面操作台 + avatar + 配置)
3. 7-Zip AES-256 加密合并为一个 .7z (密码读自 ~/.yingying_key)
4. 存入 F:\\OneDrive\\yingying_backups\\ (OneDrive 自动云同步 → 新电脑可见)
5. 保留最近 4 份, 删除旧备份; 7z t 验证完整性; 写日志

用法: python yingying_backup.py [--keep N]
被 hermes backup 自动包含 → 每周日 cron 调用。
"""
import argparse
import datetime
import os
import shutil
import secrets
import string
import subprocess
import sys
import zipfile

HERMES_HOME = os.environ.get("HERMES_HOME", os.path.expandvars(r"%LOCALAPPDATA%\hermes"))
YINGYING_HOME = r"E:\yingying-home"
KEY_FILE = os.path.join(os.path.expanduser("~"), ".yingying_key")
SEVEN_ZIP = r"C:\Program Files\7-Zip\7z.exe"
LOG_FILE = os.path.join(HERMES_HOME, "logs", "yingying_backup.log")


def find_backup_dir() -> str:
    """自动探测 OneDrive 备份目录 (优先 F:\\OneDrive, 回退用户 OneDrive)."""
    for cand in [r"F:\OneDrive",
                 os.path.expandvars(r"%USERPROFILE%\OneDrive"),
                 os.path.expandvars(r"%USERPROFILE%\OneDrive - University of Dundee")]:
        if os.path.isdir(cand):
            return os.path.join(cand, "yingying_backups")
    return os.path.expandvars(r"%USERPROFILE%\OneDrive\yingying_backups")


BACKUP_DIR = find_backup_dir()

# yingying-home 打包时排除的内容
EXCLUDE_YINGYING = {".git", "__pycache__", "chat_images", "backups"}
EXCLUDE_YINGYING_SUFFIX = (".pyc",)


def log(msg: str):
    line = f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_or_create_key() -> str:
    """读取备份密码; 不存在则生成 24 位随机强密码."""
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    key = "".join(secrets.choice(alphabet) for _ in range(24))
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key)
    try:
        # 仅本用户可读 (Windows: 通过 icacls 收紧权限; 失败不阻塞)
        subprocess.run(
            ["icacls", KEY_FILE, "/inheritance:r", "/grant:r", f"{os.getlogin()}:F"],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass
    log(f"已生成新备份密码并存入 {KEY_FILE} —— 请主人把密码记到密码管理器!")
    return key


def find_hermes() -> str:
    """定位 hermes 可执行."""
    exe = shutil.which("hermes")
    if exe:
        return exe
    cand = os.path.join(HERMES_HOME, "hermes-agent", "venv", "Scripts", "hermes.exe")
    if os.path.exists(cand):
        return cand
    return "hermes"


def find_7z() -> str:
    if os.path.exists(SEVEN_ZIP):
        return SEVEN_ZIP
    exe = shutil.which("7z") or shutil.which("7za")
    if exe:
        return exe
    raise FileNotFoundError("7-Zip 未安装 (需要 C:\\Program Files\\7-Zip\\7z.exe)")


def zip_yingying_home(out_zip: str) -> int:
    """打包 E:\\yingying-home (排除 .git/__pycache__/chat_images/backups)."""
    if not os.path.isdir(YINGYING_HOME):
        log(f"跳过: {YINGYING_HOME} 不存在")
        return 0
    count = 0
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, dirs, files in os.walk(YINGYING_HOME):
            rel_root = os.path.relpath(root, YINGYING_HOME)
            dirs[:] = [d for d in dirs if d not in EXCLUDE_YINGYING]
            for f in files:
                if f.endswith(EXCLUDE_YINGYING_SUFFIX):
                    continue
                full = os.path.join(root, f)
                arc = os.path.join("yingying-home", rel_root, f)
                try:
                    z.write(full, arc)
                    count += 1
                except OSError as e:
                    log(f"跳过 {full}: {e}")
    log(f"荧荧桌面资产打包: {count} 个文件 -> {out_zip}")
    return count


def _run(cmd, timeout=600):
    """运行子进程, 兼容中文 Windows 的 GBK 输出."""
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="gbk", errors="replace", timeout=timeout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=4, help="保留最近 N 份备份 (默认 4)")
    args = ap.parse_args()

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = os.path.join(os.environ.get("TEMP", "/tmp"), f"yingying_backup_{ts}")
    os.makedirs(tmp, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)

    try:
        key = load_or_create_key()
        seven_zip = find_7z()
        hermes_exe = find_hermes()

        # 1) Hermes 完整配置备份
        hermes_zip = os.path.join(tmp, f"hermes_{ts}.zip")
        log("开始 hermes backup ...")
        r = _run([hermes_exe, "backup", "-o", hermes_zip], timeout=600)
        if r.returncode != 0 or not os.path.exists(hermes_zip):
            log(f"hermes backup 失败 rc={r.returncode}: {r.stderr[-2000:]}")
            return 1
        size_mb = os.path.getsize(hermes_zip) / 1e6
        log(f"hermes backup OK: {size_mb:.1f} MB")

        # 2) 荧荧外部资产
        ying_zip = os.path.join(tmp, f"yingying_home_{ts}.zip")
        zip_yingying_home(ying_zip)

        # 3) 7z AES-256 加密合并 (默认 .7z 格式, -mhe=on 加密文件头)
        final_7z = os.path.join(tmp, f"yingying_backup_{ts}.7z")
        cmd = [seven_zip, "a", f"-p{key}", "-mhe=on",
               final_7z, hermes_zip, ying_zip]
        r = _run(cmd, timeout=900)
        if r.returncode != 0:
            log(f"7z 加密失败 rc={r.returncode}: {r.stdout[-2000:]}")
            return 1

        # 4) 移入 OneDrive 备份目录
        dest = os.path.join(BACKUP_DIR, os.path.basename(final_7z))
        shutil.move(final_7z, dest)
        log(f"已存入: {dest} ({os.path.getsize(dest)/1e6:.1f} MB)")

        # 5) 完整性验证
        r = _run([seven_zip, "t", f"-p{key}", dest], timeout=600)
        ok = r.returncode == 0 and "Everything is Ok" in r.stdout
        log("7z 完整性验证: " + ("OK" if ok else f"FAILED rc={r.returncode}"))
        if not ok:
            return 1

        # 6) 清理旧备份 (保留最近 N 份)
        backups = sorted(
            f for f in os.listdir(BACKUP_DIR) if f.startswith("yingying_backup_") and f.endswith(".7z")
        )
        for old in backups[:-args.keep]:
            os.remove(os.path.join(BACKUP_DIR, old))
            log(f"清理旧备份: {old}")

        log("==== 荧荧备份完成 ====")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
