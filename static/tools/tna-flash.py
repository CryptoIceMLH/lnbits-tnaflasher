#!/usr/bin/env python3
"""
TNA-OS Flash Tool — Installs TNA-OS firmware on Antminer S19 XP

Usage:
    python tna-flash.py

Requirements:
    pip install paramiko requests

This tool contains NO firmware. It verifies your flash code with the TNA server,
then has your miner download each firmware file directly from the server via curl.
"""

import sys
import time

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

TNA_INIT_SCRIPT = b"""#!/bin/sh
MINER_APP=/tna-miner
case "$1" in
  start)
    echo "Starting TNA-OS..."
    killall httpd 2>/dev/null
    start-stop-daemon -S -o --background -m --pidfile /var/run/tna-miner.pid \
        --startas /bin/sh -- -c "exec $MINER_APP > /var/volatile/tna.log 2>&1"
    sleep 3
    pgrep -f tna-miner > /dev/null && echo "TNA-OS started"
    ;;
  stop)
    start-stop-daemon -K -q -x $MINER_APP
    ;;
  restart)
    $0 stop; sleep 2; $0 start
    ;;
esac
"""


def ssh_exec(ssh, cmd, timeout=60):
    stdin, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    stdout.channel.recv_exit_status()
    return stdout.read().decode("utf-8", errors="replace").strip()


def ssh_write_file(ssh, remote_path, data):
    """Write small binary data via dd — only used for the tiny init script."""
    transport = ssh.get_transport()
    channel = transport.open_session()
    channel.exec_command(f"dd of={remote_path} bs=1 count={len(data)} 2>/dev/null")
    sent = 0
    while sent < len(data):
        n = channel.send(data[sent:])
        sent += n
    channel.shutdown_write()
    time.sleep(1)
    channel.recv(1024)
    channel.close()
    return sent


def main():
    print("=" * 50)
    print("  TNA-OS Flash Tool")
    print("  Zero Fee Bitcoin Mining Firmware")
    print("=" * 50)
    print()

    miner_ip = input("Enter miner IP address: ").strip()
    if not miner_ip:
        print("ERROR: No IP provided")
        sys.exit(1)

    flash_code = input("Enter flash code: ").strip().upper()
    if not flash_code:
        print("ERROR: No flash code provided")
        sys.exit(1)

    # ── Step 1: Verify code ──
    print()
    print("[1/6] Verifying flash code...")
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
        device = result["device"]
        version = result["version"]
        print(f"  Code valid: {device} {version}")
    except Exception as e:
        print(f"ERROR: Could not verify code: {e}")
        sys.exit(1)

    # ── Step 2: Get file list from server ──
    print("[2/6] Getting firmware file list...")
    file_url = f"{SERVER_URL}/tnaflasher/api/v1/flash/file"
    try:
        resp = requests.get(
            f"{SERVER_URL}/tnaflasher/api/v1/flash/filelist",
            params={"code": flash_code},
            timeout=30
        )
        files = resp.json().get("files", [])
        print(f"  {len(files)} files to install")
    except Exception as e:
        print(f"ERROR: Could not get file list: {e}")
        sys.exit(1)

    # ── Step 3: Connect to miner via SSH ──
    print(f"[3/6] Connecting to miner at {miner_ip}...")
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(miner_ip, port=SSH_PORT, username=SSH_USER, password=SSH_PASS, timeout=10)
        print("  Connected")
    except Exception as e:
        print(f"ERROR: SSH connection failed: {e}")
        sys.exit(1)

    # ── Step 4: Prepare miner ──
    print("[4/6] Preparing miner...")
    ssh_exec(ssh, "killall -9 tna-miner bmminer cgminer single-board-test monitorcgminer luxminer httpd 2>/dev/null; sleep 2")
    ssh_exec(ssh, "mount -o remount,rw /")
    ssh_exec(ssh, "mount -t ubifs ubi0:nvdada_log /mnt/root 2>/dev/null")
    ssh_exec(ssh, "rm -f /mnt/root/etc/rc5.d/S*luxminer* /mnt/root/etc/rc5.d/S*bmminer* "
                  "/mnt/root/etc/rc5.d/S*single-board-test* /mnt/root/etc/rc5.d/S*monitorcgminer* 2>/dev/null")
    print("  Done")

    # ── Step 5: Miner downloads each file from server via curl ──
    print("[5/6] Installing TNA-OS (miner downloading from server)...")

    for f in files:
        name = f.lstrip("./")
        url = f"{file_url}?code={flash_code}&file={name}"

        if name == "tna-miner":
            print(f"  Downloading binary...")
            result = ssh_exec(ssh, f'curl -sf -o /tna-miner "{url}" && chmod +x /tna-miner && ls -lh /tna-miner', timeout=120)
            print(f"    {result}")

        elif name == "tna-miner-init":
            # Init script goes to NAND via dd (tiny file, safe)
            print("  Installing init script on NAND...")
            n = ssh_write_file(ssh, "/mnt/root/etc/init.d/tna-miner", TNA_INIT_SCRIPT)
            ssh_exec(ssh, "chmod +x /mnt/root/etc/init.d/tna-miner")
            ssh_exec(ssh, "ln -sf ../init.d/tna-miner /mnt/root/etc/rc5.d/S90tna-miner")
            verify = ssh_exec(ssh, "head -1 /mnt/root/etc/init.d/tna-miner")
            print(f"    Written {n} bytes — {'verified' if 'sh' in verify else 'WARNING: check failed'}")

        elif name == "tna-os.toml":
            exists = ssh_exec(ssh, "test -f /config/tna-os.toml && echo exists")
            if "exists" in exists:
                print("  Config exists — keeping current settings")
            else:
                print("  Downloading default config...")
                ssh_exec(ssh, "mkdir -p /config")
                ssh_exec(ssh, f'curl -sf -o /config/tna-os.toml "{url}"', timeout=30)

        elif name.startswith("firmware/"):
            remote = "/" + name
            print(f"  {name}")
            ssh_exec(ssh, f'mkdir -p $(dirname {remote})')
            ssh_exec(ssh, f'curl -sf -o {remote} "{url}"', timeout=30)

    # Cleanup old LuxOS artifacts
    ssh_exec(ssh, "rm -f /luxminer /luxupdate /luxminer.disabled /luxupdate.disabled "
                  "/mnt/root/etc/init.d/luxminer-init 2>/dev/null")

    # ── Step 6: Sync NAND ──
    print("[6/6] Syncing to NAND...")
    ssh_exec(ssh, "sync && sync && sync")
    time.sleep(3)
    ssh_exec(ssh, "sync")

    ssh.close()

    print()
    print("=" * 50)
    print("  TNA-OS installed successfully!")
    print(f"  Power cycle your miner, then open:")
    print(f"  http://{miner_ip}")
    print("=" * 50)


if __name__ == '__main__':
    main()
