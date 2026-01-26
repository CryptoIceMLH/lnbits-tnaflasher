import time
from typing import Optional
from uuid import uuid4

from . import db
from .models import FlashRequest, Bulletin, PromoCode, Miner, Firmware


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

async def create_promo_code(code: str, discount_percent: int, max_uses: int) -> PromoCode:
    """Create a new promo code"""
    promo_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.promo_codes (id, code, discount_percent, max_uses, used_count, active, created_at)
        VALUES (:id, :code, :discount_percent, :max_uses, 0, TRUE, :created_at)
        """,
        {
            "id": promo_id,
            "code": code.upper(),
            "discount_percent": discount_percent,
            "max_uses": max_uses,
            "created_at": now
        }
    )

    return PromoCode(
        id=promo_id,
        code=code.upper(),
        discount_percent=discount_percent,
        max_uses=max_uses,
        used_count=0,
        active=True,
        created_at=now
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


async def validate_promo_code(code: str) -> tuple[bool, int, str]:
    """
    Validate a promo code.
    Returns: (is_valid, discount_percent, message)
    """
    promo = await get_promo_code_by_code(code)

    if not promo:
        return (False, 0, "Invalid promo code")

    if not promo.active:
        return (False, 0, "Promo code is inactive")

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

async def create_miner(name: str) -> Miner:
    """Create a new miner"""
    miner_id = str(uuid4())
    now = int(time.time())

    await db.execute(
        """
        INSERT INTO tnaflasher.miners (id, name, created_at)
        VALUES (:id, :name, :created_at)
        """,
        {"id": miner_id, "name": name, "created_at": now}
    )

    return Miner(
        id=miner_id,
        name=name,
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
