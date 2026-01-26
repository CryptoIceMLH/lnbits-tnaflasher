async def m001_create_flash_requests(db):
    """Create the flash_requests table to track all flash payment attempts"""
    await db.execute(
        """
        CREATE TABLE tnaflasher.flash_requests (
            id TEXT PRIMARY KEY,
            payment_hash TEXT UNIQUE NOT NULL,
            bolt11 TEXT NOT NULL,
            device TEXT NOT NULL,
            version TEXT NOT NULL,
            amount_sats INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            token_used BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            paid_at TIMESTAMP,
            flashed_at TIMESTAMP
        )
        """
    )


async def m002_create_settings(db):
    """Create the settings table for configuration"""
    await db.execute(
        """
        CREATE TABLE tnaflasher.settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Insert default price
    await db.execute(
        """
        INSERT INTO tnaflasher.settings (key, value) VALUES ('price_sats', '5000')
        """
    )
    # Insert default wallet (empty - must be configured)
    await db.execute(
        """
        INSERT INTO tnaflasher.settings (key, value) VALUES ('wallet_id', '')
        """
    )


async def m003_create_bulletins(db):
    """Create the bulletins table for news/updates on the public page"""
    await db.execute(
        """
        CREATE TABLE tnaflasher.bulletins (
            id TEXT PRIMARY KEY,
            message TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


async def m004_create_promo_codes(db):
    """Create the promo_codes table for discount codes"""
    await db.execute(
        """
        CREATE TABLE tnaflasher.promo_codes (
            id TEXT PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            discount_percent INTEGER NOT NULL,
            max_uses INTEGER NOT NULL,
            used_count INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


async def m005_create_miners(db):
    """Create the miners table for dynamic miner management"""
    await db.execute(
        """
        CREATE TABLE tnaflasher.miners (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


async def m006_create_firmware(db):
    """Create the firmware table for per-miner firmware with individual pricing"""
    await db.execute(
        """
        CREATE TABLE tnaflasher.firmware (
            id TEXT PRIMARY KEY,
            miner_id TEXT NOT NULL REFERENCES tnaflasher.miners(id) ON DELETE CASCADE,
            version TEXT NOT NULL,
            price_sats INTEGER NOT NULL,
            notes TEXT,
            discount_enabled BOOLEAN NOT NULL DEFAULT TRUE,
            file_path TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(miner_id, version)
        )
        """
    )
