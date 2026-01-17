import time
from typing import Optional
from uuid import uuid4

from . import db
from .models import FlashRequest, Setting


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
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (request_id, payment_hash, bolt11, device, version, amount_sats, now)
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
        SELECT id, payment_hash, bolt11, device, version, amount_sats,
               status, token_used, created_at, paid_at, flashed_at
        FROM tnaflasher.flash_requests
        WHERE payment_hash = ?
        """,
        (payment_hash,)
    )

    if not row:
        return None

    return FlashRequest(
        id=row[0],
        payment_hash=row[1],
        bolt11=row[2],
        device=row[3],
        version=row[4],
        amount_sats=row[5],
        status=row[6],
        token_used=bool(row[7]),
        created_at=row[8],
        paid_at=row[9],
        flashed_at=row[10]
    )


async def mark_flash_paid(payment_hash: str) -> Optional[FlashRequest]:
    """Mark a flash request as paid"""
    now = int(time.time())

    await db.execute(
        """
        UPDATE tnaflasher.flash_requests
        SET status = 'paid', paid_at = ?
        WHERE payment_hash = ? AND status = 'pending'
        """,
        (now, payment_hash)
    )

    return await get_flash_request(payment_hash)


async def mark_token_used(payment_hash: str) -> bool:
    """Mark the flash token as used (firmware downloaded)"""
    await db.execute(
        """
        UPDATE tnaflasher.flash_requests
        SET token_used = TRUE
        WHERE payment_hash = ? AND status = 'paid'
        """,
        (payment_hash,)
    )
    return True


async def mark_flash_complete(payment_hash: str) -> Optional[FlashRequest]:
    """Mark a flash request as complete (device flashed)"""
    now = int(time.time())

    await db.execute(
        """
        UPDATE tnaflasher.flash_requests
        SET status = 'flashed', flashed_at = ?
        WHERE payment_hash = ? AND status = 'paid'
        """,
        (now, payment_hash)
    )

    return await get_flash_request(payment_hash)


async def get_all_flash_requests(limit: int = 100) -> list[FlashRequest]:
    """Get all flash requests, most recent first"""
    rows = await db.fetchall(
        """
        SELECT id, payment_hash, bolt11, device, version, amount_sats,
               status, token_used, created_at, paid_at, flashed_at
        FROM tnaflasher.flash_requests
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (limit,)
    )

    return [
        FlashRequest(
            id=row[0],
            payment_hash=row[1],
            bolt11=row[2],
            device=row[3],
            version=row[4],
            amount_sats=row[5],
            status=row[6],
            token_used=bool(row[7]),
            created_at=row[8],
            paid_at=row[9],
            flashed_at=row[10]
        )
        for row in rows
    ]


async def get_stats() -> dict:
    """Get statistics for admin dashboard"""
    # Total flashes (status = 'flashed')
    total_row = await db.fetchone(
        """
        SELECT COUNT(*), COALESCE(SUM(amount_sats), 0)
        FROM tnaflasher.flash_requests
        WHERE status = 'flashed'
        """
    )
    total_flashes = total_row[0] if total_row else 0
    total_sats = total_row[1] if total_row else 0

    # Today's flashes
    today_start = int(time.time()) - (int(time.time()) % 86400)
    today_row = await db.fetchone(
        """
        SELECT COUNT(*)
        FROM tnaflasher.flash_requests
        WHERE status = 'flashed' AND flashed_at >= ?
        """,
        (today_start,)
    )
    today_flashes = today_row[0] if today_row else 0

    # Pending count
    pending_row = await db.fetchone(
        """
        SELECT COUNT(*)
        FROM tnaflasher.flash_requests
        WHERE status = 'pending'
        """
    )
    pending_count = pending_row[0] if pending_row else 0

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
        SELECT value FROM tnaflasher.settings WHERE key = ?
        """,
        (key,)
    )
    return row[0] if row else None


async def set_setting(key: str, value: str) -> None:
    """Set a setting value"""
    now = int(time.time())

    # Try update first
    result = await db.execute(
        """
        UPDATE tnaflasher.settings SET value = ?, updated_at = ? WHERE key = ?
        """,
        (value, now, key)
    )

    # If no rows updated, insert
    if result.rowcount == 0:
        await db.execute(
            """
            INSERT INTO tnaflasher.settings (key, value, updated_at) VALUES (?, ?, ?)
            """,
            (key, value, now)
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
