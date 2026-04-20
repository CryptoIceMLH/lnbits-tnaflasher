from fastapi import APIRouter, Query, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse
from lnbits.core.models import User
from lnbits.decorators import check_admin
from pathlib import Path
from typing import Optional

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
    CreateMiner,
    MinersResponse,
    UpdateFirmware,
    CreateAuditLog,
    AuditLogsResponse,
    VerifyCodeResponse,
)
from .crud import (
    get_all_flash_requests,
    get_flash_request,
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
    create_miner,
    get_miners,
    get_miner,
    delete_miner,
    create_firmware,
    get_firmware_by_miner,
    get_firmware,
    create_audit_log,
    clear_audit_log,
    get_firmware_by_miner_and_version,
    update_firmware,
    delete_firmware,
    get_feature_flags,
    set_feature_flag,
    get_audit_log,
    get_flash_code_by_code,
    mark_flash_code_used,
    check_rate_limit,
    record_rate_limit_attempt,
)
from .services import (
    get_available_devices,
    create_flash_invoice,
    get_flash_status,
    get_firmware_path,
    get_firmware_dir,
    verify_flash_token,
    verify_flash_code,
)

tnaflasher_api_router = APIRouter(prefix="/api/v1")


# ============== Public Endpoints ==============

@tnaflasher_api_router.get("/health")
async def api_health():
    """Health check endpoint"""
    return {"status": "ok", "service": "tnaflasher"}


@tnaflasher_api_router.get("/devices")
async def api_get_devices():
    """Get list of available devices and firmware versions"""
    devices = await get_available_devices()
    return {"devices": devices}


@tnaflasher_api_router.get("/price")
async def api_get_price() -> PriceResponse:
    """Get the current flash price (legacy - now prices are per-firmware)"""
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
        token=result.get("token"),
        flash_code=result.get("flash_code"),
        flash_code_expires_at=result.get("flash_code_expires_at"),
        flash_method=result.get("flash_method"),
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
    firmware_path = await get_firmware_path(device, version)
    if not firmware_path:
        raise HTTPException(status_code=404, detail="Firmware not found")

    # Mark token as used
    payment_hash = payload.get("payment_hash", "")
    await mark_token_used(payment_hash)

    # Resolve miner name from device ID
    miner_name = device  # fallback to UUID if lookup fails
    miner = await get_miner(device)
    if miner:
        miner_name = miner.name

    # Log to audit log
    await create_audit_log(
        wallet_id="public",
        action="firmware_download",
        details=f"Device: {miner_name}, Version: {version}, Ref: {payment_hash[:16]}...",
        device_mac=None
    )

    # Return firmware file
    return FileResponse(
        path=firmware_path,
        filename=f"{device}_{version}.bin",
        media_type="application/octet-stream"
    )


@tnaflasher_api_router.post("/flash/complete/{payment_hash}")
async def api_mark_complete(
    payment_hash: str,
    wallet_id: Optional[str] = Query(None),
    device_mac: Optional[str] = Query(None)
):
    """Mark a flash as complete (called after successful flash)"""
    result = await mark_flash_complete(payment_hash)
    if not result:
        raise HTTPException(status_code=404, detail="Flash request not found")

    # Resolve miner name from device ID
    miner_name = result.device
    if result.device:
        miner = await get_miner(result.device)
        if miner:
            miner_name = miner.name

    # Log to audit log
    await create_audit_log(
        wallet_id=wallet_id or "public",
        action="flash_complete",
        details=f"Device: {miner_name}, Version: {result.version}",
        device_mac=device_mac
    )

    return {"success": True}


# ============== Flash Code Endpoints (ASIC/SSH) ==============

@tnaflasher_api_router.get("/flash/verify-code")
async def api_verify_flash_code(
    code: str = Query(...),
    request: Request = None
) -> VerifyCodeResponse:
    """Verify a flash code (called by tna-flash.py tool)"""
    # Rate limiting
    client_ip = request.client.host if request and request.client else "unknown"
    within_limit = await check_rate_limit(client_ip, "verify-code", max_attempts=5, window_seconds=3600)
    if not within_limit:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")
    await record_rate_limit_attempt(client_ip, "verify-code")

    # Verify the code
    result = await verify_flash_code(code.upper())
    return VerifyCodeResponse(
        valid=result.get("valid", False),
        device=result.get("device"),
        version=result.get("version"),
        error=result.get("error")
    )


@tnaflasher_api_router.get("/flash/download")
async def api_download_firmware_by_code(
    code: str = Query(...),
    request: Request = None
):
    """Download firmware using a flash code (called by tna-flash.py tool)"""
    # Verify code is valid
    result = await verify_flash_code(code.upper())
    if not result.get("valid"):
        raise HTTPException(status_code=400, detail=result.get("error", "Invalid code"))

    # Get firmware file path
    device = result["device"]
    version = result["version"]
    firmware_path = await get_firmware_path(device, version)
    if not firmware_path:
        raise HTTPException(status_code=404, detail="Firmware not found")

    # Mark code as used
    client_ip = request.client.host if request and request.client else "unknown"
    await mark_flash_code_used(code.upper(), client_ip)

    # Resolve miner name for audit log
    miner = await get_miner(device)
    miner_name = miner.name if miner else device

    # Audit log
    flash_code_obj = await get_flash_code_by_code(code.upper())
    payment_ref = flash_code_obj.payment_hash[:16] if flash_code_obj else "unknown"
    await create_audit_log(
        wallet_id="public",
        action="firmware_download",
        details=f"Device: {miner_name}, Version: {version}, Code: {code.upper()}, Ref: {payment_ref}...",
        device_mac=None
    )

    # Determine media type based on file extension
    media_type = "application/gzip" if str(firmware_path).endswith((".tar.gz", ".tgz")) else "application/octet-stream"
    filename = firmware_path.name

    return FileResponse(
        path=firmware_path,
        filename=filename,
        media_type=media_type
    )


@tnaflasher_api_router.get("/flash/filelist")
async def api_list_firmware_files(
    code: str = Query(...),
    request: Request = None
):
    """Return list of files in the firmware tar and mark code as used."""
    result = await verify_flash_code(code.upper())
    if not result.get("valid"):
        raise HTTPException(status_code=403, detail=result.get("error", "Invalid or expired flash code"))

    device = result["device"]
    version = result["version"]
    firmware_path = await get_firmware_path(device, version)
    if not firmware_path:
        raise HTTPException(status_code=404, detail="Firmware not found")

    # Mark code used so it can't be reused
    client_ip = request.client.host if request and request.client else "unknown"
    await mark_flash_code_used(code.upper(), client_ip)

    # Audit log
    miner = await get_miner(device)
    miner_name = miner.name if miner else device
    flash_code_obj = await get_flash_code_by_code(code.upper())
    payment_ref = flash_code_obj.payment_hash[:16] if flash_code_obj else "unknown"
    await create_audit_log(
        wallet_id="public",
        action="ssh_flash_started",
        details=f"Device: {miner_name}, Version: {version}, Code: {code.upper()}, IP: {client_ip}, Ref: {payment_ref}...",
        device_mac=None
    )

    import tarfile
    with tarfile.open(str(firmware_path), "r:gz") as tar:
        files = [m.name for m in tar.getmembers() if m.isfile()]
    return {"files": files}


@tnaflasher_api_router.get("/flash/file")
async def api_download_single_file(
    code: str = Query(...),
    file: str = Query(...),
    request: Request = None
):
    """Serve a single file from the firmware tar by name, authenticated by flash code.
    Called by curl on the miner — code must be valid (not necessarily unused)."""
    result = await verify_flash_code(code.upper(), check_used=False)
    if not result.get("valid"):
        raise HTTPException(status_code=403, detail="Invalid or expired flash code")

    device = result["device"]
    version = result["version"]
    firmware_path = await get_firmware_path(device, version)
    if not firmware_path:
        raise HTTPException(status_code=404, detail="Firmware not found")

    import tarfile, io as _io
    with tarfile.open(str(firmware_path), "r:gz") as tar:
        # Normalize requested filename
        target = file.lstrip("./")
        for member in tar.getmembers():
            name = member.name.lstrip("./")
            if name == target:
                f = tar.extractfile(member)
                if f is None:
                    raise HTTPException(status_code=404, detail=f"File {file} not extractable")
                data = f.read()
                from fastapi.responses import Response
                return Response(content=data, media_type="application/octet-stream")
    raise HTTPException(status_code=404, detail=f"File {file} not found in firmware")


@tnaflasher_api_router.get("/tools/tna-flash.py")
async def api_get_flash_tool():
    """Download the TNA flash tool script"""
    tool_path = Path(__file__).parent / "static" / "tools" / "tna-flash.py"
    if not tool_path.exists():
        raise HTTPException(status_code=404, detail="Flash tool not found")
    return FileResponse(
        path=tool_path,
        filename="tna-flash.py",
        media_type="text/x-python"
    )


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
    """Get current price (admin only) - legacy, now prices are per-firmware"""
    price = await get_price()
    return PriceResponse(price_sats=price)


@tnaflasher_api_router.post("/admin/price")
async def api_admin_set_price(
    price_sats: int = Query(..., ge=1),
    user: User = Depends(check_admin)
):
    """Set the flash price (admin only) - legacy, now prices are per-firmware"""
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


# ============== Miner Management Endpoints ==============

@tnaflasher_api_router.get("/admin/miners")
async def api_admin_get_miners(user: User = Depends(check_admin)) -> MinersResponse:
    """Get all miners (admin only)"""
    miners = await get_miners()
    return MinersResponse(miners=miners)


@tnaflasher_api_router.post("/admin/miners")
async def api_admin_create_miner(
    data: CreateMiner,
    user: User = Depends(check_admin)
):
    """Create a new miner (admin only)"""
    if not data.name or len(data.name.strip()) == 0:
        raise HTTPException(status_code=400, detail="Miner name is required")

    miner = await create_miner(data.name.strip(), data.flash_method)
    return miner.dict()


@tnaflasher_api_router.delete("/admin/miners/{miner_id}")
async def api_admin_delete_miner(
    miner_id: str,
    user: User = Depends(check_admin)
):
    """Delete a miner and all its firmware (admin only)"""
    miner = await get_miner(miner_id)
    if not miner:
        raise HTTPException(status_code=404, detail="Miner not found")

    # Get firmware for this miner to delete files
    firmware_list = await get_firmware_by_miner(miner_id)
    for fw in firmware_list:
        try:
            stored = fw.file_path
            file_path = Path(stored) if Path(stored).is_absolute() else get_firmware_dir() / stored
            if file_path.exists():
                file_path.unlink()
        except Exception:
            pass  # Ignore file deletion errors

    await delete_miner(miner_id)
    return {"success": True}


# ============== Firmware Management Endpoints ==============

@tnaflasher_api_router.get("/admin/firmware/{miner_id}")
async def api_admin_get_firmware(
    miner_id: str,
    user: User = Depends(check_admin)
):
    """Get all firmware for a miner (admin only)"""
    miner = await get_miner(miner_id)
    if not miner:
        raise HTTPException(status_code=404, detail="Miner not found")

    firmware_list = await get_firmware_by_miner(miner_id)
    return {"firmware": [fw.dict() for fw in firmware_list]}


@tnaflasher_api_router.post("/admin/firmware/upload")
async def api_admin_upload_firmware(
    miner_id: str = Query(...),
    version: str = Query(...),
    price_sats: int = Query(..., ge=0),
    notes: str = Query(None),
    discount_enabled: bool = Query(True),
    file: UploadFile = File(...),
    user: User = Depends(check_admin)
):
    """Upload a firmware file (admin only)"""
    # Validate miner exists
    miner = await get_miner(miner_id)
    if not miner:
        raise HTTPException(status_code=400, detail=f"Unknown miner: {miner_id}")

    # Check if firmware version already exists for this miner
    existing = await get_firmware_by_miner_and_version(miner_id, version)
    if existing:
        raise HTTPException(status_code=400, detail=f"Firmware version {version} already exists for this miner")

    # Validate file extension based on flash method
    if miner.flash_method == "ssh":
        if not file.filename.endswith(".tar.gz") and not file.filename.endswith(".tgz"):
            raise HTTPException(status_code=400, detail="SSH miners require .tar.gz firmware files")
        file_ext = ".tar.gz"
    else:
        if not file.filename.endswith(".bin"):
            raise HTTPException(status_code=400, detail="WebSerial miners require .bin firmware files")
        file_ext = ".bin"

    # Create miner directory if needed
    firmware_dir = get_firmware_dir()
    miner_dir = firmware_dir / miner_id
    miner_dir.mkdir(parents=True, exist_ok=True)

    # Save the file
    file_path = miner_dir / f"{version}{file_ext}"
    content = await file.read()
    file_path.write_bytes(content)

    # Create firmware record in database (store relative path)
    firmware = await create_firmware(
        miner_id=miner_id,
        version=version,
        price_sats=price_sats,
        file_path=f"{miner_id}/{version}{file_ext}",
        notes=notes,
        discount_enabled=discount_enabled
    )

    return {
        "success": True,
        "firmware": firmware.dict(),
        "size": len(content)
    }


@tnaflasher_api_router.put("/admin/firmware/{firmware_id}")
async def api_admin_update_firmware(
    firmware_id: str,
    price_sats: int = Query(None, ge=0),
    notes: str = Query(None),
    discount_enabled: bool = Query(None),
    user: User = Depends(check_admin)
):
    """Update firmware details (admin only)"""
    firmware = await get_firmware(firmware_id)
    if not firmware:
        raise HTTPException(status_code=404, detail="Firmware not found")

    updated = await update_firmware(
        firmware_id=firmware_id,
        price_sats=price_sats,
        notes=notes,
        discount_enabled=discount_enabled
    )

    return {"success": True, "firmware": updated.dict()}


@tnaflasher_api_router.delete("/admin/firmware/{firmware_id}")
async def api_admin_delete_firmware(
    firmware_id: str,
    user: User = Depends(check_admin)
):
    """Delete a firmware file (admin only)"""
    firmware = await get_firmware(firmware_id)
    if not firmware:
        raise HTTPException(status_code=404, detail="Firmware not found")

    # Delete the file
    try:
        stored = firmware.file_path
        file_path = Path(stored) if Path(stored).is_absolute() else get_firmware_dir() / stored
        if file_path.exists():
            file_path.unlink()
    except Exception:
        pass  # Ignore file deletion errors

    # Delete from database
    await delete_firmware(firmware_id)

    return {"success": True}


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
async def api_validate_promo(code: str = Query(...), device_type: str = Query("both")) -> ValidatePromoResponse:
    """Validate a promo code for a specific device type (public endpoint)"""
    is_valid, discount_percent, message = await validate_promo_code(code, device_type=device_type)
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

    promo = await create_promo_code(data.code, data.discount_percent, data.max_uses, data.device_type)
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


# ============== Feature Flags Endpoints ==============

@tnaflasher_api_router.get("/feature-flags")
async def api_get_feature_flags() -> dict:
    """Get all feature flag settings (public endpoint)"""
    flags = await get_feature_flags()
    return flags


@tnaflasher_api_router.post("/feature-flags")
async def api_set_feature_flag(
    key: str = Query(...),
    value: bool = Query(...),
    user: User = Depends(check_admin)
):
    """Set a feature flag (admin only)"""
    # Validate key is a feature flag
    if not key.startswith("feature_"):
        raise HTTPException(status_code=400, detail="Invalid feature flag key")

    await set_feature_flag(key, value)
    return {"success": True, "key": key, "value": value}


# ============== Audit Log Endpoints ==============

@tnaflasher_api_router.post("/advanced/audit-log")
async def api_create_audit_log(
    data: CreateAuditLog,
    wallet_id: str = Query(...)
):
    """Create an audit log entry (called from advanced page browser)"""
    log_entry = await create_audit_log(
        wallet_id=wallet_id,
        action=data.action,
        details=data.details,
        device_mac=data.device_mac
    )
    return log_entry.dict()


@tnaflasher_api_router.get("/advanced/audit-log")
async def api_get_audit_log(
    limit: int = Query(50, ge=1, le=500),
    user: User = Depends(check_admin)
) -> AuditLogsResponse:
    """Get recent audit log entries (admin only)"""
    logs = await get_audit_log(limit=limit)
    return AuditLogsResponse(audit_logs=logs)


@tnaflasher_api_router.delete("/advanced/audit-log")
async def api_clear_audit_log(user: User = Depends(check_admin)):
    """Clear all audit log entries (admin only)"""
    await clear_audit_log()
    return {"success": True}
