from fastapi import APIRouter, Query, Depends, HTTPException
from fastapi.responses import FileResponse
from lnbits.core.models import User
from lnbits.decorators import check_admin

from .models import (
    CreateFlashRequest,
    FlashInvoiceResponse,
    FlashStatusResponse,
    DevicesResponse,
    PriceResponse,
    StatsResponse,
)
from .crud import (
    get_all_flash_requests,
    get_stats,
    get_price,
    set_price,
    get_wallet_id,
    set_wallet_id,
    mark_token_used,
    mark_flash_complete,
)
from .services import (
    get_available_devices,
    create_flash_invoice,
    get_flash_status,
    get_firmware_path,
    verify_flash_token,
)

tnaflasher_api_router = APIRouter(prefix="/api/v1")


# ============== Public Endpoints ==============

@tnaflasher_api_router.get("/health")
async def api_health():
    """Health check endpoint"""
    return {"status": "ok", "service": "tnaflasher"}


@tnaflasher_api_router.get("/devices")
async def api_get_devices() -> DevicesResponse:
    """Get list of available devices and firmware versions"""
    devices = get_available_devices()
    return DevicesResponse(devices=devices)


@tnaflasher_api_router.get("/price")
async def api_get_price() -> PriceResponse:
    """Get the current flash price"""
    price = await get_price()
    return PriceResponse(price_sats=price)


@tnaflasher_api_router.post("/flash/invoice")
async def api_create_invoice(
    data: CreateFlashRequest,
    wallet_id: str = Query(...)
) -> FlashInvoiceResponse:
    """Create a Lightning invoice for flashing"""
    try:
        result = await create_flash_invoice(
            device=data.device,
            version=data.version,
            wallet_id=wallet_id
        )
        return FlashInvoiceResponse(
            payment_hash=result["payment_hash"],
            bolt11=result["bolt11"],
            amount=result["amount"],
            expires_at=result["expires_at"]
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@tnaflasher_api_router.get("/flash/status/{payment_hash}")
async def api_get_status(payment_hash: str) -> FlashStatusResponse:
    """Check the status of a flash payment"""
    result = await get_flash_status(payment_hash)
    return FlashStatusResponse(
        status=result.get("status", "not_found"),
        token=result.get("token")
    )


@tnaflasher_api_router.get("/firmware/{device}/{version}")
async def api_download_firmware(
    device: str,
    version: str,
    token: str = Query(...)
):
    """Download firmware (requires valid payment token)"""
    # Verify token
    payload = verify_flash_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    # Check token matches request
    if payload.get("device") != device or payload.get("version") != version:
        raise HTTPException(status_code=401, detail="Token does not match request")

    # Get firmware path
    firmware_path = get_firmware_path(device, version)
    if not firmware_path:
        raise HTTPException(status_code=404, detail="Firmware not found")

    # Mark token as used
    await mark_token_used(payload.get("payment_hash", ""))

    # Return firmware file
    return FileResponse(
        path=firmware_path,
        filename=f"{device}_{version}.bin",
        media_type="application/octet-stream"
    )


@tnaflasher_api_router.post("/flash/complete/{payment_hash}")
async def api_mark_complete(payment_hash: str):
    """Mark a flash as complete (called after successful flash)"""
    result = await mark_flash_complete(payment_hash)
    if not result:
        raise HTTPException(status_code=404, detail="Flash request not found")
    return {"success": True}


# ============== Admin Endpoints ==============

@tnaflasher_api_router.get("/admin/requests")
async def api_admin_get_requests(user: User = Depends(check_admin)):
    """Get all flash requests (admin only)"""
    requests = await get_all_flash_requests()
    return [r.dict() for r in requests]


@tnaflasher_api_router.get("/admin/stats")
async def api_admin_get_stats(user: User = Depends(check_admin)) -> StatsResponse:
    """Get statistics (admin only)"""
    stats = await get_stats()
    return StatsResponse(**stats)


@tnaflasher_api_router.get("/admin/price")
async def api_admin_get_price(user: User = Depends(check_admin)) -> PriceResponse:
    """Get current price (admin only)"""
    price = await get_price()
    return PriceResponse(price_sats=price)


@tnaflasher_api_router.post("/admin/price")
async def api_admin_set_price(
    price_sats: int = Query(..., ge=1),
    user: User = Depends(check_admin)
):
    """Set the flash price (admin only)"""
    await set_price(price_sats)
    return {"success": True, "price_sats": price_sats}


@tnaflasher_api_router.get("/admin/wallet")
async def api_admin_get_wallet(user: User = Depends(check_admin)):
    """Get configured wallet ID (admin only)"""
    wallet_id = await get_wallet_id()
    return {"wallet_id": wallet_id}


@tnaflasher_api_router.post("/admin/wallet")
async def api_admin_set_wallet(
    wallet_id: str = Query(...),
    user: User = Depends(check_admin)
):
    """Set the wallet ID (admin only)"""
    await set_wallet_id(wallet_id)
    return {"success": True, "wallet_id": wallet_id}
