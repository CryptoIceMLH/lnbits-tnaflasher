# TNA-Flash Compiled Executable — Webflasher Integration Brief

This document is a briefing for the TNA-Webflasher agent. It describes the new compiled
Go binary that replaces `tna-flash.py`, what the binary expects from the server, and
exactly what the webflasher needs to add/change to support it.

---

## What Is Being Built

A compiled Go binary (`tna-flash.exe` on Windows) that users download from the public
flash page alongside their flash code. It is a true compiled native binary — no Python,
no readable source, no bytecode extraction possible. This protects the SSH logic and
exploit chain from being copied.

The binary is built from `static/tools/tna-flash-src/main.go` (source lives in this
repo for reference, but only the compiled `.exe` is served publicly).

---

## How the New Binary Works (from the user's perspective)

```
1. User pays Lightning invoice on the public flash page
2. Flash code is shown: TNA-XXXX-XXXX
3. A "Download Flash Tool" button appears (new — webflasher must add this)
4. User downloads tna-flash.exe, runs it
5. Exe prompts: "Enter miner IP:" and "Enter flash code:"
6. Exe calls your server to verify the code and get the file list
7. Exe SSHs to the miner
8. Miner curls each firmware file directly from your server (same as today)
9. Exe syncs NAND, tells user to power cycle
```

The server-side file delivery (tar.gz → per-file extraction via `/flash/file`) is
**unchanged**. The binary just replaces the Python script the user was running.

---

## Server API the Binary Uses (no changes needed to existing endpoints)

The Go binary calls the same endpoints `tna-flash.py` already calls:

| Endpoint | Used for |
|----------|----------|
| `GET /tnaflasher/api/v1/flash/verify-code?code=TNA-XXXX-XXXX` | Validate code, get device+version |
| `GET /tnaflasher/api/v1/flash/filelist?code=TNA-XXXX-XXXX` | Get list of files in firmware package |
| `GET /tnaflasher/api/v1/flash/file?code=TNA-XXXX-XXXX&file=tna-miner` | Per-file download URL (miner curls this directly) |

**These endpoints already exist and work correctly. No changes needed.**

The binary constructs the per-file URL as:
```
https://lnbits.molonlabe.holdings/tnaflasher/api/v1/flash/file?code={code}&file={filename}
```
and passes this URL to the miner via SSH so the miner curls it directly.

---

## What the Webflasher Agent Needs to Add

### 1. New endpoint: serve the compiled binary

**File: `views_api.py`**

Add this endpoint. The binary is stored at `static/tools/tna-flash.exe`:

```python
@tnaflasher_api_router.get("/tools/tna-flash.exe")
async def api_download_flash_tool():
    """Serve the compiled tna-flash Windows executable."""
    path = Path(__file__).parent / "static" / "tools" / "tna-flash.exe"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Flash tool binary not available yet")
    return FileResponse(
        path,
        filename="tna-flash.exe",
        media_type="application/octet-stream"
    )
```

Also **remove or admin-gate** the old Python source endpoint — search `views_api.py`
for any route that serves `tna-flash.py` and either delete it or wrap it in
`@Depends(check_admin)` so the source is no longer publicly downloadable.

---

### 2. Download button on the public flash page

**File: `templates/tnaflasher/public_page.html`**

Find the section where the flash code is displayed for SSH devices (search for
`flash_code` in the Vue template). Directly below that code display block, add:

```html
<!-- tna-flash.exe download — shown only for SSH/ASIC devices after payment -->
<div v-if="flashResult && flashResult.flash_method === 'ssh'" class="q-mt-md">
  <div class="text-subtitle2 q-mb-xs">Step 2: Download Flash Tool</div>
  <q-btn
    icon="download"
    label="Download tna-flash.exe (Windows)"
    href="/tnaflasher/api/v1/tools/tna-flash.exe"
    color="orange"
    unelevated
    no-caps
  />
  <div class="text-caption text-grey q-mt-xs">
    No install required. Run it, enter your miner IP and the code above.
  </div>
</div>
```

The button only appears for SSH-method devices (ASIC miners). WebSerial devices
(ESP32) are unaffected — they flash via browser as before.

---

### 3. Place the compiled binary in the repo

**Path: `static/tools/tna-flash.exe`**

The TNA-OS project (separate repo) builds this binary and commits it here. The
webflasher just needs to serve whatever file exists at that path. If the file is
not there yet, the endpoint returns 404 gracefully (handled above).

The Go source lives at `static/tools/tna-flash-src/main.go` for reference but is
NOT served publicly — only the compiled `.exe` is.

---

## What the Binary Does on the Miner (SSH sequence)

This is what the Go binary sends to the miner over SSH, in order. The webflasher
agent does not need to implement this — it's inside the binary — but it's here for
reference so the server can verify expected behaviour:

```bash
# Kill any running miners
killall -9 tna-miner bmminer cgminer single-board-test monitorcgminer luxminer httpd 2>/dev/null

# For tna-miner binary:
curl -sf -o /tna-miner "{file_url}?code={code}&file=tna-miner"
chmod +x /tna-miner

# For tna-miner-init (init script):
curl -sf -o /tmp/tna-miner-init "{file_url}?code={code}&file=tna-miner-init"
cp /tmp/tna-miner-init /etc/init.d/tna-miner
chmod +x /etc/init.d/tna-miner
ln -sf ../init.d/tna-miner /etc/rc5.d/S90tna-miner

# For tna-os.toml (only if not already present):
test -f /config/tna-os.toml || curl -sf -o /config/tna-os.toml "{file_url}?code={code}&file=tna-os.toml"

# For firmware/* files:
mkdir -p /firmware/assets/i18n
curl -sf -o /firmware/{filename} "{file_url}?code={code}&file=firmware/{filename}"

# Cleanup old LuxOS artifacts
rm -f /luxminer /luxupdate /mnt/root/etc/init.d/luxminer-init 2>/dev/null

# NAND sync
sync && sync && sync
sleep 3
sync
```

The miner downloads everything directly from `lnbits.molonlabe.holdings` — the user's
machine is just the SSH relay, no firmware bytes pass through it.

---

## Summary of Changes for Webflasher Agent

| File | Change |
|------|--------|
| `views_api.py` | Add `GET /tools/tna-flash.exe` endpoint (FileResponse) |
| `views_api.py` | Remove or admin-gate old `GET /tools/tna-flash.py` endpoint |
| `templates/tnaflasher/public_page.html` | Add download button after SSH flash code display |
| `static/tools/tna-flash.exe` | Place compiled binary here (provided by TNA-OS project) |
| `static/tools/tna-flash-src/main.go` | Place Go source here (for reference, not served) |

No database migrations needed. No changes to services.py, crud.py, tasks.py, or models.py.
The existing flash code verification and file delivery API is unchanged.
