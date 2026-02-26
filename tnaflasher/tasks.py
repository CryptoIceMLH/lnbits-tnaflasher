import asyncio

from lnbits.core.models import Payment
from lnbits.tasks import register_invoice_listener

from .crud import mark_flash_paid


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
    await mark_flash_paid(payment.payment_hash)
