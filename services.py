import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from lnbits.core.services import create_invoice
from lnbits.settings import settings

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
    create_audit_log,
    create_flash_code as crud_create_flash_code,
    get_flash_code_by_payment_hash,
)


# Token expiry time (5 minutes)
TOKEN_EXPIRY_SECONDS = 300

# Flash code expiry time (30 minutes)
FLASH_CODE_EXPIRY_SECONDS = 1800

# Secret for signing tokens (in production, use environment variable)
TOKEN_SECRET = os.environ.get("TNAFLASHER_SECRET", "change-this-secret-in-production")


def get_firmware_dir() -> Path:
    """Get the firmware directory path (persistent data volume)"""
    firmware_dir = Path(settings.lnbits_data_folder) / "tnaflasher" / "firmware"
    firmware_dir.mkdir(parents=True, exist_ok=True)
    return firmware_dir


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
            "flash_method": miner.flash_method,
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

    # Support both legacy absolute paths and new relative paths
    stored = firmware.file_path
    firmware_path = Path(stored) if Path(stored).is_absolute() else get_firmware_dir() / stored

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
    stored = firmware.file_path
    firmware_path = Path(stored) if Path(stored).is_absolute() else get_firmware_dir() / stored
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

        is_valid, discount_percent, message = await validate_promo_code(promo_code, device_type=miner.flash_method)
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

        # Log to audit log
        promo_info = f", Promo: {promo_code} ({discount_percent}% off)" if promo_code else " (price was 0)"
        await create_audit_log(
            wallet_id=wallet_id,
            action="flash_paid",
            details=f"Device: {miner.name}, Version: {version}, Amount: 0 sats (FREE){promo_info}",
            device_mac=None
        )

        # Increment promo code usage
        if promo_code:
            await increment_promo_usage(promo_code)

        # Generate flash code for SSH devices (ASIC miners)
        if miner.flash_method == "ssh":
            await create_flash_code_for_payment(free_hash, device, version)

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

    # Log to audit log
    promo_info = f", Promo: {promo_code} ({discount_percent}% off, base: {base_price} sats)" if promo_code else ""
    await create_audit_log(
        wallet_id=wallet_id,
        action="invoice_created",
        details=f"Device: {miner.name}, Version: {version}, Amount: {final_price} sats{promo_info}",
        device_mac=None
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
        # Check if this is an SSH device — return flash code instead of token
        miner = await get_miner(request.device)
        if miner and miner.flash_method == "ssh":
            flash_code = await get_flash_code_by_payment_hash(payment_hash)
            if not flash_code and request.status == "paid":
                # Race condition: payment confirmed but code not yet generated
                flash_code_obj = await create_flash_code_for_payment(
                    payment_hash, request.device, request.version
                )
                return {
                    "status": request.status,
                    "flash_code": flash_code_obj.code,
                    "flash_code_expires_at": flash_code_obj.expires_at,
                    "flash_method": "ssh"
                }
            if flash_code:
                return {
                    "status": request.status,
                    "flash_code": flash_code.code,
                    "flash_code_expires_at": flash_code.expires_at,
                    "flash_method": "ssh"
                }
            return {"status": request.status, "flash_method": "ssh"}

        # WebSerial (ESP32) and WebUSB (K230) — both are browser-driven and use
        # a payment token. webusb downloads the .kdimg with it; webserial flashes
        # over Web Serial. The page already knows the method from the device, but
        # we echo it back for clarity.
        method = miner.flash_method if miner else "webserial"
        if not request.token_used:
            token = generate_flash_token(
                request.payment_hash,
                request.device,
                request.version
            )
            return {"status": request.status, "token": token, "flash_method": method}
        else:
            return {"status": request.status, "token_used": True, "flash_method": method}

    return {"status": request.status}


# ============== Flash Code Functions (ASIC/SSH) ==============

def generate_flash_code() -> str:
    """Generate a random flash code in format TNA-XXXX-XXXX"""
    # Exclude ambiguous characters: 0/O, 1/I/L
    chars = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
    part1 = ''.join(secrets.choice(chars) for _ in range(4))
    part2 = ''.join(secrets.choice(chars) for _ in range(4))
    return f"TNA-{part1}-{part2}"


async def create_flash_code_for_payment(
    payment_hash: str,
    device: str,
    version: str
) -> "FlashCode":
    """Generate and store a flash code after payment confirmation"""
    code = generate_flash_code()
    expires_at = int(time.time()) + FLASH_CODE_EXPIRY_SECONDS

    return await crud_create_flash_code(
        code=code,
        payment_hash=payment_hash,
        device=device,
        version=version,
        expires_at=expires_at
    )


async def verify_flash_code(code: str, check_used: bool = True) -> dict:
    """Verify a flash code is valid. If check_used=False, allow already-used codes (for file downloads)."""
    from .crud import get_flash_code_by_code

    flash_code = await get_flash_code_by_code(code)

    if not flash_code:
        return {"valid": False, "error": "Invalid code"}

    if check_used and flash_code.status == "used":
        return {"valid": False, "error": "Code already used"}

    if flash_code.status == "expired":
        return {"valid": False, "error": "Code expired"}

    now = int(time.time())
    if flash_code.expires_at and flash_code.expires_at < now:
        return {"valid": False, "error": "Code expired"}

    return {
        "valid": True,
        "device": flash_code.device,
        "version": flash_code.version
    }
