from fastapi import APIRouter, Query, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from lnbits.core.models import User
from lnbits.decorators import check_admin
from pathlib import Path

from .models import (
    CreateFlashRequest,
    FlashInvoiceResponse,
    FlashStatusResponse,
    DevicesResponse,
    PriceResponse,
    StatsResponse,
    CreateBulletin,
    BulletinsResponse,
    CreatePromoCode,
    PromoCodesResponse,
    ValidatePromoResponse,
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
    create_bulletin,
    get_bulletins,
    update_bulletin,
    delete_bulletin,
    create_promo_code,
    get_promo_codes,
    validate_promo_code,
    update_promo_code,
    delete_promo_code,
)
from .services import (
    get_available_devices,
    create_flash_invoice,
    get_flash_status,
    get_firmware_path,
    get_firmware_dir,
    verify_flash_token,
    SUPPORTED_DEVICES,
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
            wallet_id=wallet_id,
            promo_code=data.promo_code
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


@tnaflasher_api_router.post("/admin/firmware/upload")
async def api_admin_upload_firmware(
    device: str = Query(...),
    version: str = Query(...),
    file: UploadFile = File(...),
    user: User = Depends(check_admin)
):
    """Upload a firmware file (admin only)"""
    # Validate device
    if device not in SUPPORTED_DEVICES:
        raise HTTPException(status_code=400, detail=f"Unknown device: {device}")

    # Validate file extension
    if not file.filename.endswith(".bin"):
        raise HTTPException(status_code=400, detail="File must be a .bin file")

    # Create device directory if needed
    firmware_dir = get_firmware_dir()
    device_dir = firmware_dir / device
    device_dir.mkdir(parents=True, exist_ok=True)

    # Save the file
    file_path = device_dir / f"{version}.bin"
    content = await file.read()
    file_path.write_bytes(content)

    return {
        "success": True,
        "device": device,
        "version": version,
        "size": len(content)
    }


@tnaflasher_api_router.delete("/admin/firmware/{device}/{version}")
async def api_admin_delete_firmware(
    device: str,
    version: str,
    user: User = Depends(check_admin)
):
    """Delete a firmware file (admin only)"""
    # Validate device
    if device not in SUPPORTED_DEVICES:
        raise HTTPException(status_code=400, detail=f"Unknown device: {device}")

    # Get firmware path
    firmware_path = get_firmware_path(device, version)
    if not firmware_path:
        raise HTTPException(status_code=404, detail="Firmware not found")

    # Delete the file
    firmware_path.unlink()

    return {"success": True, "device": device, "version": version}


# ============== Bulletin Endpoints ==============

@tnaflasher_api_router.get("/bulletins")
async def api_get_bulletins() -> BulletinsResponse:
    """Get active bulletins for public display"""
    bulletins = await get_bulletins(active_only=True)
    return BulletinsResponse(bulletins=bulletins)


@tnaflasher_api_router.get("/admin/bulletins")
async def api_admin_get_bulletins(user: User = Depends(check_admin)) -> BulletinsResponse:
    """Get all bulletins including inactive (admin only)"""
    bulletins = await get_bulletins(active_only=False)
    return BulletinsResponse(bulletins=bulletins)


@tnaflasher_api_router.post("/admin/bulletins")
async def api_admin_create_bulletin(
    data: CreateBulletin,
    user: User = Depends(check_admin)
):
    """Create a new bulletin (admin only)"""
    bulletin = await create_bulletin(data.message)
    return bulletin.dict()


@tnaflasher_api_router.put("/admin/bulletins/{bulletin_id}")
async def api_admin_update_bulletin(
    bulletin_id: str,
    message: str = Query(None),
    active: bool = Query(None),
    user: User = Depends(check_admin)
):
    """Update a bulletin (admin only)"""
    bulletin = await update_bulletin(bulletin_id, message=message, active=active)
    if not bulletin:
        raise HTTPException(status_code=404, detail="Bulletin not found")
    return bulletin.dict()


@tnaflasher_api_router.delete("/admin/bulletins/{bulletin_id}")
async def api_admin_delete_bulletin(
    bulletin_id: str,
    user: User = Depends(check_admin)
):
    """Delete a bulletin (admin only)"""
    await delete_bulletin(bulletin_id)
    return {"success": True}


# ============== Promo Code Endpoints ==============

@tnaflasher_api_router.post("/validate-promo")
async def api_validate_promo(code: str = Query(...)) -> ValidatePromoResponse:
    """Validate a promo code (public endpoint)"""
    is_valid, discount_percent, message = await validate_promo_code(code)
    return ValidatePromoResponse(
        valid=is_valid,
        discount_percent=discount_percent,
        message=message
    )


@tnaflasher_api_router.get("/admin/promo-codes")
async def api_admin_get_promo_codes(user: User = Depends(check_admin)) -> PromoCodesResponse:
    """Get all promo codes (admin only)"""
    promo_codes = await get_promo_codes()
    return PromoCodesResponse(promo_codes=promo_codes)


@tnaflasher_api_router.post("/admin/promo-codes")
async def api_admin_create_promo_code(
    data: CreatePromoCode,
    user: User = Depends(check_admin)
):
    """Create a new promo code (admin only)"""
    if data.discount_percent < 1 or data.discount_percent > 100:
        raise HTTPException(status_code=400, detail="Discount must be between 1 and 100")
    if data.max_uses < 1:
        raise HTTPException(status_code=400, detail="Max uses must be at least 1")

    promo = await create_promo_code(data.code, data.discount_percent, data.max_uses)
    return promo.dict()


@tnaflasher_api_router.put("/admin/promo-codes/{promo_id}")
async def api_admin_update_promo_code(
    promo_id: str,
    active: bool = Query(...),
    user: User = Depends(check_admin)
):
    """Update a promo code (toggle active status) (admin only)"""
    promo = await update_promo_code(promo_id, active=active)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo code not found")
    return promo.dict()


@tnaflasher_api_router.delete("/admin/promo-codes/{promo_id}")
async def api_admin_delete_promo_code(
    promo_id: str,
    user: User = Depends(check_admin)
):
    """Delete a promo code (admin only)"""
    await delete_promo_code(promo_id)
    return {"success": True}
