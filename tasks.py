import asyncio

from lnbits.core.models import Payment
from lnbits.tasks import register_invoice_listener

from .crud import (
    mark_flash_paid,
    create_audit_log,
    get_miner,
    expire_flash_codes,
    cleanup_rate_limits,
)
from .services import create_flash_code_for_payment


async def wait_for_paid_invoices():
    """Background task that listens for paid invoices"""
    invoice_queue = asyncio.Queue()
    register_invoice_listener(invoice_queue, "ext_tnaflasher")

    while True:
        payment = await invoice_queue.get()
        await on_invoice_paid(payment)


async def on_invoice_paid(payment: Payment) -> None:
    """Handle a paid invoice"""
    # Check if this is a tnaflasher payment
    if not payment.extra:
        return

    if payment.extra.get("tag") != "tnaflasher":
        return

    # Mark the flash request as paid
    req = await mark_flash_paid(payment.payment_hash)

    if req:
        # For SSH devices (ASIC miners), generate a flash code
        miner = await get_miner(req.device)
        if miner and miner.flash_method == "ssh":
            await create_flash_code_for_payment(
                req.payment_hash, req.device, req.version
            )

        # Log to audit log
        await create_audit_log(
            wallet_id=payment.wallet_id,
            action="flash_paid",
            details=f"Device: {req.device}, Version: {req.version}, Amount: {req.amount_sats} sats",
            device_mac=None
        )


async def expire_flash_codes_task():
    """Background task that expires old flash codes and cleans up rate limits"""
    while True:
        try:
            await expire_flash_codes()
            await cleanup_rate_limits(older_than_seconds=7200)
        except Exception:
            pass  # Don't crash on errors
        await asyncio.sleep(60)  # Check every minute
