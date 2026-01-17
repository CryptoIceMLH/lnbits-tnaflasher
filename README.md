# Tech Nerd Army Web Flasher

A LNbits extension for paywalled ESP32 firmware flashing. Users pay with Lightning to flash their NerdQAxe mining devices.

## Features

- **Lightning Payment**: Pay-per-flash using Lightning Network via LNbits
- **Web Serial Flashing**: Flash ESP32 devices directly from the browser
- **Token Protection**: Firmware only accessible after payment
- **Admin Dashboard**: Track flashes, revenue, and manage pricing
- **Multiple Devices**: Support for NerdQAxe+, NerdQAxe+2, NerdQX, NerdOCTAXE+, NerdOCTAXE Gamma

## Supported Devices

| Device | ASIC | Count |
|--------|------|-------|
| NerdQAxe+ | BM1368 | 4 |
| NerdQAxe+2 | BM1368 | 4 |
| NerdQX | BM1370 | 4 |
| NerdOCTAXE+ | BM1368 | 8 |
| NerdOCTAXE Gamma | BM1370 | 8 |

## Installation

### Install via LNbits Extension Manager

1. Open your LNbits instance
2. Go to **Extensions** → **Manage Extensions**
3. Click **Install from URL** or **Add Extension**
4. Enter the manifest URL:
   ```
   https://github.com/TechNerdArmy/lnbits-tnaflasher/raw/main/manifest.json
   ```
   Or if using GitLab:
   ```
   https://gitlab.com/YOURUSERNAME/lnbits-tnaflasher/-/raw/main/manifest.json
   ```
5. Click **Install**
6. Enable the **TNA Flasher** extension

### Manual Installation

```bash
# SSH into your server
cd /path/to/lnbits/lnbits/extensions

# Clone the repository
git clone https://github.com/TechNerdArmy/lnbits-tnaflasher.git tnaflasher

# Restart LNbits
# For Umbrel:
cd ~/umbrel/scripts && ./app restart lnbits

# For Docker:
docker restart lnbits
```

## Adding Firmware Files

Place firmware binary files in the static firmware directory:

```
tnaflasher/
└── static/
    └── firmware/
        ├── NerdQAxePlus/
        │   ├── v3.42.bin
        │   └── v3.41.bin
        ├── NerdQAxePlus2/
        │   └── v3.42.bin
        ├── NerdQX/
        │   └── v3.42.bin
        ├── NerdOCTAXEPlus/
        │   └── v3.42.bin
        └── NerdOCTAXEGamma/
            └── v3.42.bin
```

Firmware files should be factory images (complete flash including bootloader).

## Configuration

1. Go to the admin dashboard: `/tnaflasher/`
2. Select the receiving wallet from your LNbits wallets
3. Set the flash price in SATS
4. Copy the public URL to share with users

## Usage

### For Users

1. Visit the public flasher URL: `/tnaflasher/{wallet_id}`
2. Connect device via USB (requires Chrome, Edge, Brave, or Opera)
3. Select device type and firmware version
4. Pay the Lightning invoice
5. Click "Start Flashing" after payment confirms

### For Admins

- View statistics: total flashes, revenue, today's flashes
- Monitor all flash requests with status
- Update pricing
- Manage firmware files

## API Endpoints

### Public

- `GET /api/v1/health` - Health check
- `GET /api/v1/devices` - List available devices and versions
- `GET /api/v1/price` - Get current flash price
- `POST /api/v1/flash/invoice?wallet_id=xxx` - Create flash invoice
- `GET /api/v1/flash/status/{payment_hash}` - Check payment status
- `GET /api/v1/firmware/{device}/{version}?token=xxx` - Download firmware
- `POST /api/v1/flash/complete/{payment_hash}` - Mark flash complete

### Admin (requires authentication)

- `GET /api/v1/admin/requests` - List all flash requests
- `GET /api/v1/admin/stats` - Get statistics
- `GET/POST /api/v1/admin/price` - Get/set price
- `GET/POST /api/v1/admin/wallet` - Get/set wallet

## Security

- Firmware is protected by signed tokens
- Tokens expire after 5 minutes
- Each token is single-use (marked after download)
- Payment verification via LNbits payment listener

## Browser Requirements

Web Serial API is required for flashing. Supported browsers:
- Google Chrome
- Microsoft Edge
- Brave
- Opera

Firefox and Safari are NOT supported.

## Repository Structure

```
lnbits-tnaflasher/
├── manifest.json           ← LNbits extension manifest (REQUIRED at root)
└── tnaflasher/             ← Extension folder
    ├── __init__.py         ← Extension entry point
    ├── config.json         ← Extension metadata
    ├── models.py           ← Pydantic data models
    ├── migrations.py       ← Database schema
    ├── crud.py             ← Database operations
    ├── services.py         ← Business logic
    ├── views.py            ← HTML routes
    ├── views_api.py        ← REST API endpoints
    ├── tasks.py            ← Payment listener
    ├── helpers.py          ← Utility functions
    ├── README.md           ← This file
    ├── static/
    │   ├── images/
    │   ├── js/
    │   └── firmware/       ← Place .bin files here
    ├── templates/
    │   └── tnaflasher/
    │       ├── index.html       ← Admin dashboard
    │       └── public_page.html ← Public flasher
    └── tests/
```

## License

MIT License - Tech Nerd Army

## Support

For issues and questions, contact Tech Nerd Army.
