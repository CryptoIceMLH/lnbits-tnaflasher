import time
from typing import Optional
from uuid import uuid4

from . import db
from .models import FlashRequest, Bulletin


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
