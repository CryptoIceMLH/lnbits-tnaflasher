#!/usr/bin/env python3
"""
TNA-OS Flash Tool — Installs TNA-OS firmware on Antminer S19 XP

Usage:
    python tna-flash.py

Requirements:
    pip install paramiko requests

This tool contains NO firmware. It downloads firmware from the TNA server
using a one-time flash code obtained after Lightning payment.
"""

import sys
import os
import io
import time
import tarfile

try:
    import paramiko
except ImportError:
    print("ERROR: paramiko not installed. Run: pip install paramiko")
    sys.exit(1)

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# ── Configuration ──
SERVER_URL = "https://flash.tna-os.com"  # LNBits server URL (set by agent)
SSH_USER = "root"
SSH_PASS = "root"
SSH_PORT = 22

def upload_via_dd(ssh, data, remote_path):
    """Upload file via dd — same approach as standalone flash.py, works on Dropbear."""
    parent = '/'.join(remote_path.split('/')[:-1])
    if parent:
        ssh.exec_command(f'mkdir -p {parent}')
        time.sleep(0.1)

    chan = ssh.get_transport().open_session()
    chan.exec_command(f'dd of={remote_path} bs=1 count={len(data)} 2>/dev/null')
    sent = 0
    while sent < len(data):
        n = chan.send(data[sent:])
        sent += n
    chan.shutdown_write()
    time.sleep(1)
    chan.recv(1024)
    chan.close()

def main():
    print("=" * 50)
    print("  TNA-OS Flash Tool")
    print("  Zero Fee Bitcoin Mining Firmware")
    print("=" * 50)
    print()

    # Get miner IP
    miner_ip = input("Enter miner IP address: ").strip()
    if not miner_ip:
        print("ERROR: No IP provided")
        sys.exit(1)

    # Get flash code
    flash_code = input("Enter flash code: ").strip().upper()
    if not flash_code:
        print("ERROR: No flash code provided")
        sys.exit(1)

    # ── Step 1: Verify code ──
    print()
    print("[1/5] Verifying flash code...")
    try:
        resp = requests.get(
            f"{SERVER_URL}/tnaflasher/api/v1/flash/verify-code",
            params={"code": flash_code},
            timeout=30
        )
        result = resp.json()
        if not result.get("valid"):
            print(f"ERROR: {result.get('error', 'Invalid code')}")
            sys.exit(1)
        print(f"  Code valid: {result['device']} {result['version']}")
    except Exception as e:
        print(f"ERROR: Could not verify code: {e}")
        sys.exit(1)

    # ── Step 2: Download firmware (streaming, never saved to disk) ──
    print("[2/5] Downloading firmware...")
    try:
        resp = requests.get(
            f"{SERVER_URL}/tnaflasher/api/v1/flash/download",
            params={"code": flash_code},
            stream=True,
            timeout=120
        )
        if resp.status_code != 200:
            print(f"ERROR: Download failed (HTTP {resp.status_code})")
            sys.exit(1)

        # Read into memory — never touches disk
        tar_data = io.BytesIO(resp.content)
        tar = tarfile.open(fileobj=tar_data, mode='r:gz')
        members = tar.getmembers()
        total_size = sum(m.size for m in members if m.isfile())
        print(f"  Downloaded {len(resp.content)} bytes, {len(members)} files ({total_size} bytes uncompressed)")
    except Exception as e:
        print(f"ERROR: Download failed: {e}")
        sys.exit(1)

    # ── Step 3: Connect to miner via SSH ──
    print(f"[3/5] Connecting to miner at {miner_ip}...")
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(miner_ip, port=SSH_PORT, username=SSH_USER, password=SSH_PASS, timeout=10)
        print("  Connected")
    except Exception as e:
        print(f"ERROR: SSH connection failed: {e}")
        print("  Make sure:")
        print("  - Miner is powered on and on your network")
        print("  - IP address is correct")
        print("  - SSH is enabled (LuxOS or stock firmware)")
        sys.exit(1)

    # ── Step 4: Flash firmware ──
    print("[4/5] Installing TNA-OS...")

    # Kill existing mining software
    print("  Stopping mining software...")
    ssh.exec_command('killall -9 tna-miner luxminer cgminer bmminer 2>/dev/null')
    time.sleep(2)

    # Mount filesystems writable
    ssh.exec_command('mount -o remount,rw /')
    ssh.exec_command('mount -t ubifs ubi0:nvdada_log /mnt/root 2>/dev/null')
    time.sleep(1)

    # Upload each file from tar
    file_count = 0
    for member in members:
        if not member.isfile():
            continue

        f = tar.extractfile(member)
        if f is None:
            continue
        data = f.read()
        name = member.name

        # Strip leading ./ or path prefix
        if name.startswith('./'):
            name = name[2:]

        if name == 'tna-miner':
            print(f"  Uploading binary ({len(data)} bytes)...")
            upload_via_dd(ssh, data, '/tna-miner')
            ssh.exec_command('chmod +x /tna-miner')
            file_count += 1

        elif name == 'tna-miner-init':
            print("  Installing init script...")
            upload_via_dd(ssh, data, '/mnt/root/etc/init.d/tna-miner')
            ssh.exec_command('chmod +x /mnt/root/etc/init.d/tna-miner')
            ssh.exec_command('rm -f /mnt/root/etc/rc5.d/S*luxminer* /mnt/root/etc/rc5.d/S*bmminer* /mnt/root/etc/rc5.d/S*single-board-test* /mnt/root/etc/rc5.d/S*monitorcgminer* 2>/dev/null')
            ssh.exec_command('ln -sf ../init.d/tna-miner /mnt/root/etc/rc5.d/S90tna-miner')
            file_count += 1

        elif name == 'tna-os.toml':
            # Only install default config if none exists
            stdin, stdout, stderr = ssh.exec_command('test -f /config/tna-os.toml && echo exists')
            if 'exists' not in stdout.read().decode():
                print("  Uploading default config...")
                ssh.exec_command('mkdir -p /config')
                upload_via_dd(ssh, data, '/config/tna-os.toml')
                file_count += 1
            else:
                print("  Config exists, keeping current settings")

        elif name.startswith('firmware/'):
            remote_path = '/' + name
            upload_via_dd(ssh, data, remote_path)
            file_count += 1

    print(f"  Uploaded {file_count} files")

    # Disable LuxOS if present
    ssh.exec_command('test -f /luxminer && mv /luxminer /luxminer.disabled 2>/dev/null')

    # ── Step 5: Sync NAND ──
    print("[5/5] Syncing to NAND...")
    ssh.exec_command('sync && sync && sync')
    time.sleep(3)
    ssh.exec_command('sync')
    time.sleep(1)

    ssh.close()

    print()
    print("=" * 50)
    print("  TNA-OS installed successfully!")
    print(f"  Power cycle your miner, then open:")
    print(f"  http://{miner_ip}")
    print("=" * 50)

if __name__ == '__main__':
    main()
