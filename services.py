import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Optional

from lnbits.core.services import create_invoice

from .crud import create_flash_request, get_price, get_flash_request


# Supported devices with display names
SUPPORTED_DEVICES = {
    "NerdQAxePlus": "NerdQAxe+",
    "NerdQAxePlus2": "NerdQAxe+2",
    "NerdQX": "NerdQX",
    "NerdOCTAXEPlus": "NerdOCTAXE+",
    "NerdOCTAXEGamma": "NerdOCTAXE Gamma",
}

# Token expiry time (5 minutes)
TOKEN_EXPIRY_SECONDS = 300

# Secret for signing tokens (in production, use environment variable)
TOKEN_SECRET = os.environ.get("TNAFLASHER_SECRET", "change-this-secret-in-production")


def get_firmware_dir() -> Path:
    """Get the firmware directory path"""
    return Path(__file__).parent / "static" / "firmware"


def get_available_devices() -> list[dict]:
    """Get list of available devices with their firmware versions"""
    devices = []
    firmware_dir = get_firmware_dir()

    for device_id, device_name in SUPPORTED_DEVICES.items():
        device_path = firmware_dir / device_id
        versions = []

        if device_path.exists():
            for file in device_path.glob("*.bin"):
                # Extract version from filename (e.g., v3.42.bin -> v3.42)
                version = file.stem
                versions.append(version)

        # Sort versions in descending order (newest first)
        versions.sort(reverse=True)

        devices.append({
            "id": device_id,
            "name": device_name,
            "versions": versions
        })

    return devices


def get_firmware_path(device: str, version: str) -> Optional[Path]:
    """Get the path to a firmware file"""
    if device not in SUPPORTED_DEVICES:
        return None

    firmware_path = get_firmware_dir() / device / f"{version}.bin"

    if firmware_path.exists():
        return firmware_path

    return None


async def create_flash_invoice(
    device: str,
    version: str,
    wallet_id: str
) -> dict:
    """Create a Lightning invoice for a flash request"""
    # Validate device
    if device not in SUPPORTED_DEVICES:
        raise ValueError(f"Unknown device: {device}")

    # Check firmware exists
    firmware_path = get_firmware_path(device, version)
    if not firmware_path:
        raise ValueError(f"Firmware not found: {device} {version}")

    # Get price
    price_sats = await get_price()

    # Create LNbits invoice
    payment = await create_invoice(
        wallet_id=wallet_id,
        amount=price_sats,
        memo=f"TNA Flash: {SUPPORTED_DEVICES[device]} {version}",
        extra={
            "tag": "tnaflasher",
            "device": device,
            "version": version
        }
    )

    # Store flash request in database
    await create_flash_request(
        payment_hash=payment.payment_hash,
        bolt11=payment.bolt11,
        device=device,
        version=version,
        amount_sats=price_sats
    )

    # Calculate expiry time (15 minutes from now)
    expires_at = int(time.time()) + (15 * 60)

    return {
        "payment_hash": payment.payment_hash,
        "bolt11": payment.bolt11,
        "amount": price_sats,
        "expires_at": expires_at
    }


def generate_flash_token(payment_hash: str, device: str, version: str) -> str:
    """Generate a signed token for firmware download"""
    now = int(time.time())
    expires_at = now + TOKEN_EXPIRY_SECONDS

    # Create token payload
    payload = {
        "payment_hash": payment_hash,
        "device": device,
        "version": version,
        "issued_at": now,
        "expires_at": expires_at,
        "nonce": hashlib.sha256(os.urandom(32)).hexdigest()[:16]
    }

    # Encode payload
    payload_json = json.dumps(payload, separators=(',', ':'))
    payload_b64 = payload_json.encode().hex()

    # Create signature
    signature = hmac.new(
        TOKEN_SECRET.encode(),
        payload_json.encode(),
        hashlib.sha256
    ).hexdigest()

    # Return token as payload.signature
    return f"{payload_b64}.{signature}"


def verify_flash_token(token: str) -> Optional[dict]:
    """Verify and decode a flash token"""
    try:
        # Split token
        parts = token.split(".")
        if len(parts) != 2:
            return None

        payload_b64, signature = parts

        # Decode payload
        payload_json = bytes.fromhex(payload_b64).decode()
        payload = json.loads(payload_json)

        # Verify signature
        expected_sig = hmac.new(
            TOKEN_SECRET.encode(),
            payload_json.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_sig):
            return None

        # Check expiry
        if payload.get("expires_at", 0) < int(time.time()):
            return None

        return payload

    except Exception:
        return None


async def get_flash_status(payment_hash: str) -> dict:
    """Get the status of a flash request"""
    request = await get_flash_request(payment_hash)

    if not request:
        return {"status": "not_found"}

    if request.status == "pending":
        return {"status": "pending"}

    if request.status in ("paid", "flashed"):
        # Generate token if not already used
        if not request.token_used:
            token = generate_flash_token(
                request.payment_hash,
                request.device,
                request.version
            )
            return {"status": request.status, "token": token}
        else:
            return {"status": request.status, "token_used": True}

    return {"status": request.status}
