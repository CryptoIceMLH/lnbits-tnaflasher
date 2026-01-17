# Installation Guide for TNA Flasher LNbits Extension

## Step 1: Create Git Repository

### Option A: GitHub
1. Go to [GitHub](https://github.com)
2. Click **New Repository**
3. Name: `lnbits-tnaflasher`
4. Visibility: **Public** (required for LNbits to access)
5. Click **Create repository**

### Option B: GitLab
1. Go to [GitLab](https://gitlab.com) or your self-hosted GitLab
2. Click **New Project** → **Create blank project**
3. Name: `lnbits-tnaflasher`
4. Visibility: **Public**
5. Click **Create project**

## Step 2: Upload Extension Files

### Using Git Command Line (Recommended)

```bash
# Navigate to extension folder
cd "E:\MLH BTC\ESP MINERS\ESP-Miner-NerdQAxePlus-V2-Lan\lnbits-tnaflasher"

# Initialize git
git init
git checkout -b main

# Add all files
git add .

# Commit
git commit -m "TNA Flasher extension v1.0.0 - Paywalled ESP32 web flasher"

# Add your remote (replace with your URL)
# For GitHub:
git remote add origin https://github.com/TechNerdArmy/lnbits-tnaflasher.git
# For GitLab:
git remote add origin https://gitlab.com/YOURUSERNAME/lnbits-tnaflasher.git

# Push to remote
git push -u origin main
```

## Step 3: Verify Repository Structure

Your repository should have this structure:

```
lnbits-tnaflasher/           ← Repository root
├── manifest.json            ← CRITICAL! Must be at root level
├── INSTALL_GUIDE.md
└── tnaflasher/              ← Extension folder
    ├── __init__.py
    ├── config.json
    ├── models.py
    ├── migrations.py
    ├── crud.py
    ├── services.py
    ├── views.py
    ├── views_api.py
    ├── tasks.py
    ├── helpers.py
    ├── README.md
    ├── static/
    │   ├── images/
    │   ├── js/
    │   └── firmware/
    │       ├── NerdQAxePlus/
    │       ├── NerdQAxePlus2/
    │       ├── NerdQX/
    │       ├── NerdOCTAXEPlus/
    │       └── NerdOCTAXEGamma/
    ├── templates/
    │   └── tnaflasher/
    │       ├── index.html
    │       └── public_page.html
    └── tests/
```

## Step 4: Test Manifest URL

Your manifest must be accessible at:

**GitHub:**
```
https://raw.githubusercontent.com/TechNerdArmy/lnbits-tnaflasher/main/manifest.json
```

**GitLab:**
```
https://gitlab.com/YOURUSERNAME/lnbits-tnaflasher/-/raw/main/manifest.json
```

Test it in your browser - should return:
```json
{
  "repos": [
    {
      "id": "tnaflasher",
      "organisation": "TechNerdArmy",
      "repository": "lnbits-tnaflasher"
    }
  ]
}
```

## Step 5: Install in LNbits

### Method 1: Extension Manager (Recommended)

1. Open your LNbits web interface
2. Go to **Extensions** → **Manage Extensions**
3. Look for **Install from URL** or **Add Extension**
4. Paste your manifest URL:
   ```
   https://raw.githubusercontent.com/TechNerdArmy/lnbits-tnaflasher/main/manifest.json
   ```
5. Click **Install**
6. Wait for installation to complete
7. Enable the **TNA Flasher** extension

### Method 2: Manual Install via SSH

If the extension manager doesn't work:

```bash
# SSH into your server
ssh umbrel@umbrel.local

# Navigate to LNbits extensions directory
# For Umbrel:
cd ~/umbrel/app-data/lnbits/extensions

# For standard LNbits:
cd /path/to/lnbits/lnbits/extensions

# Clone the repository - clone into 'tnaflasher' folder
git clone https://github.com/TechNerdArmy/lnbits-tnaflasher.git
mv lnbits-tnaflasher/tnaflasher .
rm -rf lnbits-tnaflasher

# Or clone directly:
git clone https://github.com/TechNerdArmy/lnbits-tnaflasher.git temp
mv temp/tnaflasher .
rm -rf temp

# Restart LNbits
# For Umbrel:
cd ~/umbrel/scripts && ./app restart lnbits

# For Docker:
docker restart lnbits

# For systemd:
sudo systemctl restart lnbits
```

## Step 6: Add Firmware Files

After installation, add your firmware binaries:

```bash
# Navigate to firmware directory
cd /path/to/lnbits/extensions/tnaflasher/static/firmware

# Copy your firmware files
cp /path/to/NerdQAxePlus_v3.42.bin NerdQAxePlus/v3.42.bin
cp /path/to/NerdQAxePlus2_v3.42.bin NerdQAxePlus2/v3.42.bin
cp /path/to/NerdQX_v3.42.bin NerdQX/v3.42.bin
cp /path/to/NerdOCTAXEPlus_v3.42.bin NerdOCTAXEPlus/v3.42.bin
cp /path/to/NerdOCTAXEGamma_v3.42.bin NerdOCTAXEGamma/v3.42.bin
```

**Important:** The filename (without .bin) becomes the version shown in the UI.
- `v3.42.bin` → shows as "v3.42"
- `v3.41.bin` → shows as "v3.41"

## Step 7: Verify Installation

### 1. Check Extension is Loaded
Open LNbits web UI and look for **TNA Flasher** in extensions list

### 2. Test API Health Check
```bash
curl http://YOUR_LNBITS_URL/tnaflasher/api/v1/health
```

Expected response:
```json
{"status":"ok","service":"tnaflasher"}
```

### 3. Test Devices Endpoint
```bash
curl http://YOUR_LNBITS_URL/tnaflasher/api/v1/devices
```

Should list your devices and available firmware versions.

## Step 8: Configure Extension

1. Open LNbits and click on **TNA Flasher**
2. Select a wallet to receive payments
3. Set your flash price (in SATS)
4. Copy the public URL to share with users

## Step 9: Share Public URL

Your public flasher URL will be:
```
https://YOUR_LNBITS_URL/tnaflasher/{wallet_id}
```

Users can visit this URL to:
1. Connect their device via USB
2. Select device and firmware version
3. Pay the Lightning invoice
4. Flash their device

## Troubleshooting

### Extension doesn't show up
- Check manifest.json is accessible at the raw URL
- Verify config.json has correct format
- Check LNbits logs for errors
- Restart LNbits service

### Database errors on first run
- Extension creates tables automatically
- Check LNbits has database permissions
- Look for migration errors in logs

### Firmware not showing
- Verify .bin files are in correct folders
- Check file permissions
- Folder names must match exactly:
  - `NerdQAxePlus` (not `NerdQAxe+`)
  - `NerdOCTAXEPlus` (not `NerdOCTAXE+`)

### API returns 404
- Verify extension is enabled
- Check extension loaded without errors
- Restart LNbits service

### Web Serial not working
- Must use Chrome, Edge, Brave, or Opera
- Firefox and Safari don't support Web Serial API
- Device must be connected via USB

## Updating Firmware

To add new firmware versions:

1. SSH into your server
2. Copy new .bin file to appropriate folder:
   ```bash
   cp new_firmware.bin /path/to/tnaflasher/static/firmware/NerdQAxePlus/v3.43.bin
   ```
3. No restart needed - new version appears immediately

## Updating Extension

To update the extension:

```bash
cd /path/to/lnbits/extensions/tnaflasher
git pull origin main

# Restart LNbits
docker restart lnbits
# or
sudo systemctl restart lnbits
```
