# TNA-Flash Compiled Executable — Webflasher Integration Brief

**Who this is for:** The TNA-Webflasher agent implementing the compiled binary support.
Read this entire document before touching any code. Everything you need to know is here —
you do not need to read the TNA-OS repo or any other project.

---

## Background — What This Project Does

TNA-Webflasher is a LNbits extension that lets users pay via Lightning Network to flash
custom Bitcoin mining firmware (TNA-OS) onto Antminer S19 XP miners. The flow is:

1. User visits the public flash page, selects their miner, pays a Lightning invoice
2. Server confirms payment and issues a one-time flash code: `TNA-XXXX-XXXX`
3. User runs a flash tool on their Windows PC with the miner's IP + flash code
4. Flash tool SSHes to the miner; miner downloads firmware files directly from the server
5. Miner reboots into TNA-OS firmware

The miner connects to the server via curl over HTTPS — the user's PC is just the SSH
relay that orchestrates the process.

---

## What Is Changing and Why

### Current state (what exists today)

`static/tools/tna-flash.py` — a Python script served publicly at:
```
GET /tnaflasher/api/v1/tools/tna-flash.py
```

**Problem:** This is plain readable Python source. Anyone who downloads it can read the
full SSH logic, understand how we gain root access on the miner, and copy our exploit
chain. It also has a hardcoded broken init script (`TNA_INIT_SCRIPT` bytes literal at
line 36) that was never updated when the init script was fixed server-side — meaning the
server's correct init script was being silently ignored and the old broken one was
written to the miner instead.

### New state (what you are building)

`static/tools/tna-flash.exe` — a compiled Go binary served at:
```
GET /tnaflasher/api/v1/tools/tna-flash.exe
```

The Go binary is a true compiled native executable — no Python runtime, no bytecode,
no readable source. The source lives at `static/tools/tna-flash-src/main.go` (already
written, see below) but only the compiled `.exe` is served publicly.

The old Python source endpoint is removed so it's no longer publicly accessible.

---

## How the Firmware Delivery Works (DO NOT CHANGE THIS)

The server stores firmware as a `.tar.gz` uploaded via the admin panel. When a user's
flash tool calls the file endpoints, the server extracts individual files from that
tarball on the fly and streams them.

The tarball has this exact flat layout (no wrapping directory):
```
tna-miner          ← ARM32 mining daemon binary (~5MB)
tna-miner-init     ← sysvinit init script for /etc/init.d/
tna-os.toml        ← default miner config
firmware/          ← Angular web UI bundle
firmware/index.html
firmware/main.*.js
firmware/runtime.*.js
firmware/polyfills.*.js
firmware/styles.*.css
firmware/assets/
firmware/assets/helmet.png
firmware/assets/i18n/en.json
... (etc)
```

**The tarball layout is correct and does not need changing.** The Go binary uses the
same three API endpoints the old Python script used — nothing changes server-side for
file delivery.

---

## API Endpoints the Go Binary Calls (already exist, no changes needed)

### 1. Verify flash code
```
GET /tnaflasher/api/v1/flash/verify-code?code=TNA-XXXX-XXXX
```
Response:
```json
{"valid": true, "device": "s19xp_v1", "version": "v0.3.1"}
```
or on failure:
```json
{"valid": false, "error": "invalid or expired code"}
```

### 2. Get file list
```
GET /tnaflasher/api/v1/flash/filelist?code=TNA-XXXX-XXXX
```
Response:
```json
{"files": ["tna-miner", "tna-miner-init", "tna-os.toml", "firmware/index.html", ...]}
```
Note: calling this endpoint marks the code as used (one-time). This is existing behaviour.

### 3. Download individual file (the miner curls this URL directly)
```
GET /tnaflasher/api/v1/flash/file?code=TNA-XXXX-XXXX&file=tna-miner
GET /tnaflasher/api/v1/flash/file?code=TNA-XXXX-XXXX&file=firmware/index.html
```
Returns raw binary/text file contents. The Go binary constructs this URL and passes it
to the miner via SSH so the miner curls it directly — the user's PC does not download
the firmware bytes.

**None of these endpoints need any changes.**

---

## What the Go Binary Does on the Miner (SSH sequence)

This is the exact sequence the compiled binary executes over SSH. Documented here so
you understand what the binary is doing — you don't implement this, it's inside the
compiled binary already.

SSH credentials: `root:root` on port `22`. The miner runs Dropbear SSH (no SFTP
subsystem — but the binary doesn't use SFTP, only exec channels).

```bash
# Step 1: Kill any running miners to free resources
killall -9 tna-miner bmminer cgminer single-board-test monitorcgminer luxminer httpd 2>/dev/null
sleep 1

# Step 2: For each file in the list from the server:

# tna-miner (the main binary, ~5MB):
curl -sf -o /tna-miner "https://server/api/v1/flash/file?code=XXX&file=tna-miner"
chmod +x /tna-miner

# tna-miner-init (the init script — fetched from server, NOT hardcoded):
curl -sf -o /tmp/tna-miner-init "https://server/api/v1/flash/file?code=XXX&file=tna-miner-init"
cp /tmp/tna-miner-init /etc/init.d/tna-miner
chmod +x /etc/init.d/tna-miner
ln -sf ../init.d/tna-miner /etc/rc5.d/S90tna-miner

# tna-os.toml (default config — only written if not already present):
test -f /config/tna-os.toml || curl -sf -o /config/tna-os.toml "https://server/.../tna-os.toml"

# firmware/* (Angular web UI files — miner writes to /firmware/):
mkdir -p /firmware/assets/i18n
curl -sf -o /firmware/index.html "https://server/.../firmware/index.html"
curl -sf -o /firmware/main.*.js "..."
# ... (all firmware/* files from the list)

# Step 3: Clean up old LuxOS artifacts (previous firmware brand)
rm -f /luxminer /luxupdate /luxminer.disabled /mnt/root/etc/init.d/luxminer-init 2>/dev/null

# Step 4: Sync filesystem to NAND (critical — power cut before this = data loss)
sync && sync && sync
sleep 3
sync
```

**Key fix vs the old Python script:** The old `tna-flash.py` had `TNA_INIT_SCRIPT`
hardcoded as a bytes literal and wrote that to the miner instead of the server's copy.
This meant init script fixes on the server were never picked up. The Go binary
correctly curls `tna-miner-init` from the server so whatever is in the tarball is
what gets installed.

---

## The Go Source (already written — at static/tools/tna-flash-src/main.go)

The Go source is already committed to this repo at:
```
static/tools/tna-flash-src/main.go
static/tools/tna-flash-src/go.mod
```

You do not need to write the Go code. The compiled binary (`static/tools/tna-flash.exe`)
is produced by running this on a Windows machine (or cross-compiling):
```bash
cd static/tools/tna-flash-src
go mod tidy
GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o ../tna-flash.exe .
```

The TNA-OS project owner will provide the compiled `tna-flash.exe` — you just need to
serve it once it exists at `static/tools/tna-flash.exe`.

---

## Your 3 Tasks

### Task 1 — Add endpoint to serve the compiled binary

**File: `views_api.py`**

Add this new endpoint. It serves the compiled Windows binary for download:

```python
@tnaflasher_api_router.get("/tools/tna-flash.exe")
async def api_download_flash_tool():
    """Serve compiled tna-flash Windows executable."""
    path = Path(__file__).parent / "static" / "tools" / "tna-flash.exe"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Flash tool binary not yet available")
    return FileResponse(
        path,
        filename="tna-flash.exe",
        media_type="application/octet-stream"
    )
```

`Path` is already imported at the top of `views_api.py`. `FileResponse` is already
imported too. This is a drop-in addition — no other files need changing for this task.

### Task 2 — Remove or gate the old Python source endpoint

**File: `views_api.py`**

Search for any route that serves `tna-flash.py` (there may be one, or it may be served
as a static file directly). If it exists as an explicit route, either:
- Delete it entirely (preferred), or
- Wrap it with `@Depends(check_admin)` so it's no longer publicly accessible

The file `static/tools/tna-flash.py` itself can stay on disk for reference — just
don't serve it publicly.

### Task 3 — Add download button to the public flash page

**File: `templates/tnaflasher/public_page.html`**

This is a Vue 3 + Quasar UI app. Find the section in the Vue template where the flash
code is displayed after a successful payment for SSH devices. It will look something
like:
```html
<div v-if="flashResult.flash_code">
  <!-- flash code display -->
</div>
```
or check for `flash_method === 'ssh'` or `flashResult.flash_code`.

Directly after that block (still inside the same payment-confirmed section), add:

```html
<div v-if="flashResult && flashResult.flash_method === 'ssh'" class="q-mt-md">
  <div class="text-subtitle2 q-mb-xs">Step 2: Download Flash Tool</div>
  <q-btn
    icon="download"
    label="Download tna-flash.exe (Windows)"
    href="/tnaflasher/api/v1/tools/tna-flash.exe"
    color="orange"
    unelevated
    no-caps
    type="a"
  />
  <div class="text-caption text-grey q-mt-xs">
    No Python install required. Run it, enter your miner IP and the code above.
  </div>
</div>
```

This button only appears for SSH/ASIC miners (Antminer S19 XP). WebSerial devices
(ESP32 Nerd miners) are completely unaffected — they flash via browser WebSerial as
before and don't use this tool at all.

---

## Files Changed Summary

| File | What to do |
|------|-----------|
| `views_api.py` | Add `GET /tools/tna-flash.exe` endpoint (Task 1) |
| `views_api.py` | Remove/gate old `GET /tools/tna-flash.py` endpoint (Task 2) |
| `templates/tnaflasher/public_page.html` | Add download button after SSH flash code (Task 3) |
| `static/tools/tna-flash.exe` | Place here when binary is provided — just serve it |
| `static/tools/tna-flash.py` | Keep on disk, stop serving it publicly |

**No other files need changing.** No database migrations. No changes to `services.py`,
`crud.py`, `tasks.py`, `models.py`, or any other backend file. The entire server-side
flash code + file delivery system is unchanged.

---

## Verification Checklist

After implementing all 3 tasks:

1. `GET /tnaflasher/api/v1/tools/tna-flash.py` → should return 404 or require admin auth
2. `GET /tnaflasher/api/v1/tools/tna-flash.exe` → returns 404 if binary not yet placed
   (graceful), returns the `.exe` file once binary is in `static/tools/`
3. Public flash page: pay for a WebSerial device → download button does NOT appear
4. Public flash page: pay for an SSH/ASIC device → download button DOES appear below
   the flash code
5. Clicking the download button → browser downloads `tna-flash.exe`
6. Running `tna-flash.exe` with a valid code and miner IP → flashes successfully
