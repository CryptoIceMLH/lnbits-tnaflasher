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
