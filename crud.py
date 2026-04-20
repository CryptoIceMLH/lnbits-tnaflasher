import time
from typing import Optional
from uuid import uuid4

from . import db
from .models import FlashRequest, Bulletin, PromoCode, Miner, Firmware, AuditLog, FlashCode


# ============== Flash Requests ==============

async def create_flash_request(
    payment_hash: str,
    bolt11: str,
    device: str,
    version: str,
    amount_sats: int
) -> FlashRequest:
    """Create a new flash request record"""
    request_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.flash_requests
        (id, payment_hash, bolt11, device, version, amount_sats, status, created_at)
        VALUES (:id, :payment_hash, :bolt11, :device, :version, :amount_sats, 'pending', :created_at)
        """,
        {
            "id": request_id,
            "payment_hash": payment_hash,
            "bolt11": bolt11,
            "device": device,
            "version": version,
            "amount_sats": amount_sats,
            "created_at": now
        }
    )

    return FlashRequest(
        id=request_id,
        payment_hash=payment_hash,
        bolt11=bolt11,
        device=device,
        version=version,
        amount_sats=amount_sats,
        status="pending",
        token_used=False,
        created_at=now
    )


async def get_flash_request(payment_hash: str) -> Optional[FlashRequest]:
    """Get a flash request by payment hash"""
    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.flash_requests
        WHERE payment_hash = :payment_hash
        """,
        {"payment_hash": payment_hash}
    )

    if not row:
        return None

    return FlashRequest(**row)


async def mark_flash_paid(payment_hash: str) -> Optional[FlashRequest]:
    """Mark a flash request as paid"""
    now = int(time.time())

    await db.execute(
        """
        UPDATE tnaflasher.flash_requests
        SET status = 'paid', paid_at = :paid_at
        WHERE payment_hash = :payment_hash AND status = 'pending'
        """,
        {"paid_at": now, "payment_hash": payment_hash}
    )

    return await get_flash_request(payment_hash)


async def mark_token_used(payment_hash: str) -> bool:
    """Mark the flash token as used (firmware downloaded)"""
    await db.execute(
        """
        UPDATE tnaflasher.flash_requests
        SET token_used = TRUE
        WHERE payment_hash = :payment_hash AND status = 'paid'
        """,
        {"payment_hash": payment_hash}
    )
    return True


async def mark_flash_complete(payment_hash: str) -> Optional[FlashRequest]:
    """Mark a flash request as complete (device flashed)"""
    now = int(time.time())

    await db.execute(
        """
        UPDATE tnaflasher.flash_requests
        SET status = 'flashed', flashed_at = :flashed_at
        WHERE payment_hash = :payment_hash AND status = 'paid'
        """,
        {"flashed_at": now, "payment_hash": payment_hash}
    )

    return await get_flash_request(payment_hash)


async def get_all_flash_requests(limit: int = 100) -> list[FlashRequest]:
    """Get all flash requests, most recent first"""
    rows = await db.fetchall(
        """
        SELECT * FROM tnaflasher.flash_requests
        ORDER BY created_at DESC
        LIMIT :limit
        """,
        {"limit": limit}
    )

    return [FlashRequest(**row) for row in rows]


async def get_stats() -> dict:
    """Get statistics for admin dashboard"""
    # Total flashes (status = 'flashed')
    total_row = await db.fetchone(
        """
        SELECT COUNT(*) as count, COALESCE(SUM(amount_sats), 0) as total
        FROM tnaflasher.flash_requests
        WHERE status = 'flashed'
        """
    )
    total_flashes = total_row["count"] if total_row else 0
    total_sats = total_row["total"] if total_row else 0

    # Today's flashes
    today_start = int(time.time()) - (int(time.time()) % 86400)
    today_row = await db.fetchone(
        """
        SELECT COUNT(*) as count
        FROM tnaflasher.flash_requests
        WHERE status = 'flashed' AND flashed_at >= :today_start
        """,
        {"today_start": today_start}
    )
    today_flashes = today_row["count"] if today_row else 0

    # Pending count
    pending_row = await db.fetchone(
        """
        SELECT COUNT(*) as count
        FROM tnaflasher.flash_requests
        WHERE status = 'pending'
        """
    )
    pending_count = pending_row["count"] if pending_row else 0

    return {
        "total_flashes": total_flashes,
        "total_sats": total_sats,
        "today_flashes": today_flashes,
        "pending_count": pending_count
    }


# ============== Settings ==============

async def get_setting(key: str) -> Optional[str]:
    """Get a setting value by key"""
    row = await db.fetchone(
        """
        SELECT value FROM tnaflasher.settings WHERE key = :key
        """,
        {"key": key}
    )
    return row["value"] if row else None


async def set_setting(key: str, value: str) -> None:
    """Set a setting value (upsert)"""
    now = int(time.time())

    # Check if exists
    existing = await get_setting(key)

    if existing is not None:
        await db.execute(
            """
            UPDATE tnaflasher.settings SET value = :value, updated_at = :updated_at WHERE key = :key
            """,
            {"value": value, "updated_at": now, "key": key}
        )
    else:
        await db.execute(
            """
            INSERT INTO tnaflasher.settings (key, value, updated_at) VALUES (:key, :value, :updated_at)
            """,
            {"key": key, "value": value, "updated_at": now}
        )


async def get_price() -> int:
    """Get the current flash price in sats"""
    value = await get_setting("price_sats")
    return int(value) if value else 5000


async def set_price(price_sats: int) -> None:
    """Set the flash price"""
    await set_setting("price_sats", str(price_sats))


async def get_wallet_id() -> Optional[str]:
    """Get the configured wallet ID"""
    value = await get_setting("wallet_id")
    return value if value else None


async def set_wallet_id(wallet_id: str) -> None:
    """Set the wallet ID"""
    await set_setting("wallet_id", wallet_id)


# ============== Bulletins ==============

async def create_bulletin(message: str) -> Bulletin:
    """Create a new bulletin"""
    bulletin_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.bulletins (id, message, active, created_at)
        VALUES (:id, :message, TRUE, :created_at)
        """,
        {"id": bulletin_id, "message": message, "created_at": now}
    )

    return Bulletin(
        id=bulletin_id,
        message=message,
        active=True,
        created_at=now
    )


async def get_bulletins(active_only: bool = True) -> list[Bulletin]:
    """Get all bulletins, optionally only active ones"""
    if active_only:
        rows = await db.fetchall(
            """
            SELECT * FROM tnaflasher.bulletins
            WHERE active = TRUE
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
    else:
        rows = await db.fetchall(
            """
            SELECT * FROM tnaflasher.bulletins
            ORDER BY created_at DESC
            LIMIT 50
            """
        )

    return [Bulletin(**row) for row in rows]


async def update_bulletin(bulletin_id: str, message: str = None, active: bool = None) -> Optional[Bulletin]:
    """Update a bulletin"""
    updates = []
    params = {"id": bulletin_id}

    if message is not None:
        updates.append("message = :message")
        params["message"] = message

    if active is not None:
        updates.append("active = :active")
        params["active"] = active

    if not updates:
        return None

    await db.execute(
        f"""
        UPDATE tnaflasher.bulletins
        SET {', '.join(updates)}
        WHERE id = :id
        """,
        params
    )

    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.bulletins WHERE id = :id
        """,
        {"id": bulletin_id}
    )

    return Bulletin(**row) if row else None


async def delete_bulletin(bulletin_id: str) -> bool:
    """Delete a bulletin"""
    await db.execute(
        """
        DELETE FROM tnaflasher.bulletins WHERE id = :id
        """,
        {"id": bulletin_id}
    )
    return True


# ============== Promo Codes ==============

async def create_promo_code(code: str, discount_percent: int, max_uses: int, device_type: str = "both") -> PromoCode:
    """Create a new promo code"""
    promo_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.promo_codes (id, code, discount_percent, max_uses, used_count, active, created_at, device_type)
        VALUES (:id, :code, :discount_percent, :max_uses, 0, TRUE, :created_at, :device_type)
        """,
        {
            "id": promo_id,
            "code": code.upper(),
            "discount_percent": discount_percent,
            "max_uses": max_uses,
            "created_at": now,
            "device_type": device_type
        }
    )

    return PromoCode(
        id=promo_id,
        code=code.upper(),
        discount_percent=discount_percent,
        max_uses=max_uses,
        used_count=0,
        active=True,
        created_at=now,
        device_type=device_type
    )


async def get_promo_codes() -> list[PromoCode]:
    """Get all promo codes for admin"""
    rows = await db.fetchall(
        """
        SELECT * FROM tnaflasher.promo_codes
        ORDER BY created_at DESC
        """
    )
    return [PromoCode(**row) for row in rows]


async def get_promo_code_by_code(code: str) -> Optional[PromoCode]:
    """Get a promo code by its code string"""
    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.promo_codes
        WHERE code = :code
        """,
        {"code": code.upper()}
    )
    return PromoCode(**row) if row else None


async def validate_promo_code(code: str, device_type: str = "both") -> tuple[bool, int, str]:
    """
    Validate a promo code for a specific device type.
    Returns: (is_valid, discount_percent, message)
    """
    promo = await get_promo_code_by_code(code)

    if not promo:
        return (False, 0, "Invalid promo code")

    if not promo.active:
        return (False, 0, "Promo code is inactive")

    if promo.device_type != "both" and promo.device_type != device_type:
        return (False, 0, "Promo code not valid for this device type")

    if promo.used_count >= promo.max_uses:
        return (False, 0, "Promo code has reached its usage limit")

    return (True, promo.discount_percent, f"{promo.discount_percent}% discount applied!")


async def increment_promo_usage(code: str) -> bool:
    """Increment the usage count for a promo code"""
    await db.execute(
        """
        UPDATE tnaflasher.promo_codes
        SET used_count = used_count + 1
        WHERE code = :code
        """,
        {"code": code.upper()}
    )
    return True


async def update_promo_code(promo_id: str, active: bool = None) -> Optional[PromoCode]:
    """Update a promo code (toggle active status)"""
    if active is not None:
        await db.execute(
            """
            UPDATE tnaflasher.promo_codes
            SET active = :active
            WHERE id = :id
            """,
            {"active": active, "id": promo_id}
        )

    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.promo_codes WHERE id = :id
        """,
        {"id": promo_id}
    )
    return PromoCode(**row) if row else None


async def delete_promo_code(promo_id: str) -> bool:
    """Delete a promo code"""
    await db.execute(
        """
        DELETE FROM tnaflasher.promo_codes WHERE id = :id
        """,
        {"id": promo_id}
    )
    return True


# ============== Miners ==============

async def create_miner(name: str, flash_method: str = "webserial") -> Miner:
    """Create a new miner"""
    miner_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.miners (id, name, flash_method, created_at)
        VALUES (:id, :name, :flash_method, :created_at)
        """,
        {"id": miner_id, "name": name, "flash_method": flash_method, "created_at": now}
    )

    return Miner(
        id=miner_id,
        name=name,
        flash_method=flash_method,
        created_at=now
    )


async def get_miners() -> list[Miner]:
    """Get all miners"""
    rows = await db.fetchall(
        """
        SELECT * FROM tnaflasher.miners
        ORDER BY name ASC
        """
    )
    return [Miner(**row) for row in rows]


async def get_miner(miner_id: str) -> Optional[Miner]:
    """Get a miner by ID"""
    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.miners WHERE id = :id
        """,
        {"id": miner_id}
    )
    return Miner(**row) if row else None


async def get_miner_by_name(name: str) -> Optional[Miner]:
    """Get a miner by name"""
    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.miners WHERE name = :name
        """,
        {"name": name}
    )
    return Miner(**row) if row else None


async def delete_miner(miner_id: str) -> bool:
    """Delete a miner and all its firmware (cascade)"""
    # First delete all firmware for this miner
    await db.execute(
        """
        DELETE FROM tnaflasher.firmware WHERE miner_id = :miner_id
        """,
        {"miner_id": miner_id}
    )
    # Then delete the miner
    await db.execute(
        """
        DELETE FROM tnaflasher.miners WHERE id = :id
        """,
        {"id": miner_id}
    )
    return True


# ============== Firmware ==============

async def create_firmware(
    miner_id: str,
    version: str,
    price_sats: int,
    file_path: str,
    notes: Optional[str] = None,
    discount_enabled: bool = True
) -> Firmware:
    """Create a new firmware entry"""
    firmware_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.firmware (id, miner_id, version, price_sats, notes, discount_enabled, file_path, created_at)
        VALUES (:id, :miner_id, :version, :price_sats, :notes, :discount_enabled, :file_path, :created_at)
        """,
        {
            "id": firmware_id,
            "miner_id": miner_id,
            "version": version,
            "price_sats": price_sats,
            "notes": notes,
            "discount_enabled": discount_enabled,
            "file_path": file_path,
            "created_at": now
        }
    )

    return Firmware(
        id=firmware_id,
        miner_id=miner_id,
        version=version,
        price_sats=price_sats,
        notes=notes,
        discount_enabled=discount_enabled,
        file_path=file_path,
        created_at=now
    )


async def get_firmware_by_miner(miner_id: str) -> list[Firmware]:
    """Get all firmware for a miner"""
    rows = await db.fetchall(
        """
        SELECT * FROM tnaflasher.firmware
        WHERE miner_id = :miner_id
        ORDER BY created_at DESC
        """,
        {"miner_id": miner_id}
    )
    return [Firmware(**row) for row in rows]


async def get_firmware(firmware_id: str) -> Optional[Firmware]:
    """Get firmware by ID"""
    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.firmware WHERE id = :id
        """,
        {"id": firmware_id}
    )
    return Firmware(**row) if row else None


async def get_firmware_by_miner_and_version(miner_id: str, version: str) -> Optional[Firmware]:
    """Get firmware by miner ID and version"""
    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.firmware
        WHERE miner_id = :miner_id AND version = :version
        """,
        {"miner_id": miner_id, "version": version}
    )
    return Firmware(**row) if row else None


async def update_firmware(
    firmware_id: str,
    price_sats: Optional[int] = None,
    notes: Optional[str] = None,
    discount_enabled: Optional[bool] = None
) -> Optional[Firmware]:
    """Update firmware details"""
    updates = []
    params = {"id": firmware_id}

    if price_sats is not None:
        updates.append("price_sats = :price_sats")
        params["price_sats"] = price_sats

    if notes is not None:
        updates.append("notes = :notes")
        params["notes"] = notes

    if discount_enabled is not None:
        updates.append("discount_enabled = :discount_enabled")
        params["discount_enabled"] = discount_enabled

    if not updates:
        return await get_firmware(firmware_id)

    await db.execute(
        f"""
        UPDATE tnaflasher.firmware
        SET {', '.join(updates)}
        WHERE id = :id
        """,
        params
    )

    return await get_firmware(firmware_id)


async def delete_firmware(firmware_id: str) -> bool:
    """Delete a firmware entry"""
    await db.execute(
        """
        DELETE FROM tnaflasher.firmware WHERE id = :id
        """,
        {"id": firmware_id}
    )
    return True


async def get_all_firmware() -> list[Firmware]:
    """Get all firmware entries"""
    rows = await db.fetchall(
        """
        SELECT * FROM tnaflasher.firmware
        ORDER BY miner_id, version
        """
    )
    return [Firmware(**row) for row in rows]


# ============== Feature Flags ==============

async def get_feature_flags() -> dict:
    """Get all feature flags from settings"""
    rows = await db.fetchall(
        """
        SELECT key, value FROM tnaflasher.settings
        WHERE key LIKE 'feature_%'
        """
    )

    flags = {}
    for row in rows:
        key = row["key"]
        value = row["value"]
        # Convert string "true"/"false" to boolean
        flags[key] = value.lower() == "true"

    return flags


async def set_feature_flag(key: str, value: bool) -> None:
    """Set a feature flag (upsert)"""
    value_str = "true" if value else "false"
    await set_setting(key, value_str)


# ============== Audit Log ==============

async def create_audit_log(
    wallet_id: str,
    action: str,
    details: str,
    device_mac: Optional[str] = None
) -> AuditLog:
    """Create an audit log entry"""
    log_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.audit_log (id, wallet_id, action, device_mac, details, created_at)
        VALUES (:id, :wallet_id, :action, :device_mac, :details, :created_at)
        """,
        {
            "id": log_id,
            "wallet_id": wallet_id,
            "action": action,
            "device_mac": device_mac,
            "details": details,
            "created_at": now
        }
    )

    return AuditLog(
        id=log_id,
        wallet_id=wallet_id,
        action=action,
        device_mac=device_mac,
        details=details,
        created_at=now
    )


async def get_audit_log(limit: int = 50) -> list[AuditLog]:
    """Get recent audit log entries"""
    rows = await db.fetchall(
        """
        SELECT * FROM tnaflasher.audit_log
        ORDER BY created_at DESC
        LIMIT :limit
        """,
        {"limit": limit}
    )

    return [AuditLog(**row) for row in rows]


async def clear_audit_log() -> None:
    """Clear all audit log entries"""
    await db.execute("DELETE FROM tnaflasher.audit_log")


# ============== Flash Codes (ASIC/SSH) ==============

async def create_flash_code(
    code: str,
    payment_hash: str,
    device: str,
    version: str,
    expires_at: int
) -> FlashCode:
    """Create a one-time flash code for SSH-based flashing"""
    code_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.flash_codes
        (id, code, payment_hash, device, version, status, created_at, expires_at)
        VALUES (:id, :code, :payment_hash, :device, :version, 'unused', :created_at, :expires_at)
        """,
        {
            "id": code_id,
            "code": code,
            "payment_hash": payment_hash,
            "device": device,
            "version": version,
            "created_at": now,
            "expires_at": expires_at
        }
    )

    return FlashCode(
        id=code_id,
        code=code,
        payment_hash=payment_hash,
        device=device,
        version=version,
        status="unused",
        created_at=now,
        expires_at=expires_at
    )


async def get_flash_code_by_code(code: str) -> Optional[FlashCode]:
    """Get a flash code by its code string"""
    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.flash_codes WHERE code = :code
        """,
        {"code": code.upper()}
    )
    return FlashCode(**row) if row else None


async def get_flash_code_by_payment_hash(payment_hash: str) -> Optional[FlashCode]:
    """Get a flash code by its linked payment hash"""
    row = await db.fetchone(
        """
        SELECT * FROM tnaflasher.flash_codes WHERE payment_hash = :payment_hash
        """,
        {"payment_hash": payment_hash}
    )
    return FlashCode(**row) if row else None


async def mark_flash_code_used(code: str, used_ip: str) -> bool:
    """Mark a flash code as used after successful firmware download"""
    now = int(time.time())

    await db.execute(
        """
        UPDATE tnaflasher.flash_codes
        SET status = 'used', used_at = :used_at, used_ip = :used_ip
        WHERE code = :code AND status = 'unused'
        """,
        {"used_at": now, "used_ip": used_ip, "code": code.upper()}
    )
    return True


async def expire_flash_codes() -> int:
    """Expire all unused flash codes past their expiry time. Returns count expired."""
    now = int(time.time())

    result = await db.execute(
        """
        UPDATE tnaflasher.flash_codes
        SET status = 'expired'
        WHERE status = 'unused' AND expires_at < :now
        """,
        {"now": now}
    )
    return 0


# ============== Rate Limiting ==============

async def check_rate_limit(
    ip_address: str,
    endpoint: str,
    max_attempts: int = 5,
    window_seconds: int = 3600
) -> bool:
    """Check if an IP is within rate limits. Returns True if allowed."""
    cutoff = int(time.time()) - window_seconds

    row = await db.fetchone(
        """
        SELECT COUNT(*) as count FROM tnaflasher.rate_limits
        WHERE ip_address = :ip AND endpoint = :endpoint AND attempted_at > :cutoff
        """,
        {"ip": ip_address, "endpoint": endpoint, "cutoff": cutoff}
    )

    count = row["count"] if row else 0
    return count < max_attempts


async def record_rate_limit_attempt(ip_address: str, endpoint: str) -> None:
    """Record a rate limit attempt"""
    attempt_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.rate_limits (id, ip_address, endpoint, attempted_at)
        VALUES (:id, :ip, :endpoint, :attempted_at)
        """,
        {"id": attempt_id, "ip": ip_address, "endpoint": endpoint, "attempted_at": now}
    )


async def cleanup_rate_limits(older_than_seconds: int = 7200) -> None:
    """Clean up old rate limit entries"""
    cutoff = int(time.time()) - older_than_seconds

    await db.execute(
        """
        DELETE FROM tnaflasher.rate_limits WHERE attempted_at < :cutoff
        """,
        {"cutoff": cutoff}
    )
