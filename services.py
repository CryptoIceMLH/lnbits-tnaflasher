import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Optional

from lnbits.core.services import create_invoice

from .crud import (
    create_flash_request,
    get_flash_request,
    validate_promo_code,
    increment_promo_usage,
    mark_flash_paid,
    get_miners,
    get_miner,
    get_firmware_by_miner,
    get_firmware_by_miner_and_version,
)


# Token expiry time (5 minutes)
TOKEN_EXPIRY_SECONDS = 300

# Secret for signing tokens (in production, use environment variable)
TOKEN_SECRET = os.environ.get("TNAFLASHER_SECRET", "change-this-secret-in-production")


def get_firmware_dir() -> Path:
    """Get the firmware directory path"""
    return Path(__file__).parent / "static" / "firmware"


async def get_available_devices() -> list[dict]:
    """Get list of available devices with their firmware versions from database"""
    devices = []
    miners = await get_miners()

    for miner in miners:
        firmware_list = await get_firmware_by_miner(miner.id)

        # Build firmware info list with prices and notes
        firmware_info = []
        for fw in firmware_list:
            firmware_info.append({
                "id": fw.id,
                "version": fw.version,
                "price_sats": fw.price_sats,
                "notes": fw.notes,
                "discount_enabled": fw.discount_enabled
            })

        devices.append({
            "id": miner.id,
            "name": miner.name,
            "firmware": firmware_info,
            # Keep versions list for backward compatibility
            "versions": [fw.version for fw in firmware_list]
        })

    return devices


async def get_firmware_path(miner_id: str, version: str) -> Optional[Path]:
    """Get the path to a firmware file"""
    firmware = await get_firmware_by_miner_and_version(miner_id, version)

    if not firmware:
        return None

    firmware_path = Path(firmware.file_path)

    if firmware_path.exists():
        return firmware_path

    return None


async def create_flash_invoice(
    device: str,
    version: str,
    wallet_id: str,
    promo_code: Optional[str] = None
) -> dict:
    """Create a Lightning invoice for a flash request"""
    # Get miner from database
    miner = await get_miner(device)
    if not miner:
        raise ValueError(f"Unknown device: {device}")

    # Get firmware for this miner and version
    firmware = await get_firmware_by_miner_and_version(device, version)
    if not firmware:
        raise ValueError(f"Firmware not found: {device} {version}")

    # Check firmware file exists
    firmware_path = Path(firmware.file_path)
    if not firmware_path.exists():
        raise ValueError(f"Firmware file not found: {device} {version}")

    # Get price from firmware (per-firmware pricing)
    base_price = firmware.price_sats
    final_price = base_price
    discount_percent = 0

    # Apply promo code if provided
    if promo_code:
        # Check if discount is enabled for this firmware
        if not firmware.discount_enabled:
            raise ValueError("Discounts are not available for this firmware")

        is_valid, discount_percent, message = await validate_promo_code(promo_code)
        if not is_valid:
            raise ValueError(message)
        # Calculate discounted price
        discount_amount = int(base_price * discount_percent / 100)
        final_price = base_price - discount_amount

    # Handle 100% discount (free flash)
    if final_price <= 0:
        # Generate a pseudo payment hash for free flashes
        free_hash = hashlib.sha256(f"{device}{version}{time.time()}{os.urandom(8).hex()}".encode()).hexdigest()

        # Store flash request as already paid
        await create_flash_request(
            payment_hash=free_hash,
            bolt11="FREE",
            device=device,
            version=version,
            amount_sats=0
        )

        # Mark as paid immediately
        await mark_flash_paid(free_hash)

        # Increment promo code usage
        if promo_code:
            await increment_promo_usage(promo_code)

        # No expiry needed for free flashes
        return {
            "payment_hash": free_hash,
            "bolt11": "FREE",
            "amount": 0,
            "expires_at": int(time.time()) + (60 * 60)  # 1 hour to complete flash
        }

    # Create LNbits invoice for paid flashes
    payment = await create_invoice(
        wallet_id=wallet_id,
        amount=final_price,
        memo=f"TNA Flash: {miner.name} {version}" + (f" ({discount_percent}% off)" if discount_percent > 0 else ""),
        extra={
            "tag": "tnaflasher",
            "device": device,
            "version": version,
            "promo_code": promo_code if promo_code else None,
            "discount_percent": discount_percent
        }
    )

    # Store flash request in database
    await create_flash_request(
        payment_hash=payment.payment_hash,
        bolt11=payment.bolt11,
        device=device,
        version=version,
        amount_sats=final_price
    )

    # Increment promo code usage (it will be counted when invoice is created)
    if promo_code:
        await increment_promo_usage(promo_code)

    # Calculate expiry time (15 minutes from now)
    expires_at = int(time.time()) + (15 * 60)

    return {
        "payment_hash": payment.payment_hash,
        "bolt11": payment.bolt11,
        "amount": final_price,
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
