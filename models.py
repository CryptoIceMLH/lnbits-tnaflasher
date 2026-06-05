from pydantic import BaseModel
from typing import Optional


class FlashRequest(BaseModel):
    """Represents a flash payment request"""
    id: str
    payment_hash: str
    bolt11: str
    device: str
    version: str
    amount_sats: int
    status: str = "pending"  # pending, paid, flashed, expired
    token_used: bool = False
    created_at: Optional[int] = None
    paid_at: Optional[int] = None
    flashed_at: Optional[int] = None


class CreateFlashRequest(BaseModel):
    """Data needed to create a flash request"""
    device: str
    version: str
    promo_code: Optional[str] = None


class FlashInvoiceResponse(BaseModel):
    """Response when creating a flash invoice"""
    payment_hash: str
    bolt11: str
    amount: int
    expires_at: int


class FlashStatusResponse(BaseModel):
    """Response for flash status check"""
    status: str
    token: Optional[str] = None
    flash_code: Optional[str] = None
    flash_code_expires_at: Optional[int] = None
    flash_method: Optional[str] = None


class DeviceInfo(BaseModel):
    """Device information"""
    id: str
    name: str
    versions: list[str]


class DevicesResponse(BaseModel):
    """List of available devices"""
    devices: list[DeviceInfo]


class PriceResponse(BaseModel):
    """Current flash price"""
    price_sats: int


class StatsResponse(BaseModel):
    """Admin statistics"""
    total_flashes: int
    total_sats: int
    today_flashes: int
    pending_count: int


class Setting(BaseModel):
    """System setting"""
    key: str
    value: str
    updated_at: Optional[int] = None


class Bulletin(BaseModel):
    """News/update bulletin for the public page"""
    id: str
    message: str
    active: bool = True
    created_at: Optional[int] = None


class CreateBulletin(BaseModel):
    """Data needed to create a bulletin"""
    message: str


class BulletinsResponse(BaseModel):
    """List of bulletins"""
    bulletins: list[Bulletin]


class PromoCode(BaseModel):
    """Promo code for discounts"""
    id: str
    code: str
    discount_percent: int  # 1-100
    max_uses: int
    used_count: int = 0
    active: bool = True
    created_at: Optional[int] = None
    device_type: str = "both"  # "webserial", "ssh", "webusb", or "both"


class CreatePromoCode(BaseModel):
    """Data needed to create a promo code"""
    code: str
    discount_percent: int
    max_uses: int
    device_type: str = "both"


class PromoCodesResponse(BaseModel):
    """List of promo codes"""
    promo_codes: list[PromoCode]


class ValidatePromoResponse(BaseModel):
    """Response for promo code validation"""
    valid: bool
    discount_percent: int = 0
    message: str = ""


# ============== Miner & Firmware Models ==============

class Miner(BaseModel):
    """Miner device type"""
    id: str
    name: str
    flash_method: str = "webserial"  # "webserial" (ESP32 Web Serial), "ssh" (ASIC/network), or "webusb" (Canaan K230 WebUSB/.kdimg)
    created_at: Optional[int] = None


class CreateMiner(BaseModel):
    """Data needed to create a miner"""
    name: str
    flash_method: str = "webserial"


class MinersResponse(BaseModel):
    """List of miners"""
    miners: list[Miner]


class Firmware(BaseModel):
    """Firmware version for a miner"""
    id: str
    miner_id: str
    version: str
    price_sats: int
    notes: Optional[str] = None
    discount_enabled: bool = True
    file_path: str
    file_size_bytes: int = 0
    created_at: Optional[int] = None


class CreateFirmware(BaseModel):
    """Data needed to create firmware (used in request body)"""
    version: str
    price_sats: int
    notes: Optional[str] = None
    discount_enabled: bool = True


class UpdateFirmware(BaseModel):
    """Data for updating firmware"""
    price_sats: Optional[int] = None
    notes: Optional[str] = None
    discount_enabled: Optional[bool] = None


class FirmwareInfo(BaseModel):
    """Firmware info for public display"""
    id: str
    version: str
    price_sats: int
    notes: Optional[str] = None
    discount_enabled: bool = True


class DeviceWithFirmware(BaseModel):
    """Device with firmware details for public page"""
    id: str
    name: str
    flash_method: str = "webserial"
    firmware: list[FirmwareInfo]


# ============== Flash Code Models (ASIC/SSH) ==============

class FlashCode(BaseModel):
    """One-time flash code for SSH-based ASIC miner flashing"""
    id: str
    code: str
    payment_hash: str
    device: str
    version: str
    status: str = "unused"  # unused, used, expired
    created_at: Optional[int] = None
    expires_at: Optional[int] = None
    used_at: Optional[int] = None
    used_ip: Optional[str] = None


class FlashCodeResponse(BaseModel):
    """Response when flash code is generated after payment"""
    code: str
    expires_at: int
    device: str
    version: str


class VerifyCodeResponse(BaseModel):
    """Response for flash code verification (called by tna-flash.py tool)"""
    valid: bool
    device: Optional[str] = None
    version: Optional[str] = None
    error: Optional[str] = None


# ============== Audit Log Models ==============

class AuditLog(BaseModel):
    """Audit log entry for tracking sensitive operations"""
    id: str
    wallet_id: str
    action: str
    device_mac: Optional[str] = None
    details: str
    created_at: Optional[int] = None


class CreateAuditLog(BaseModel):
    """Data needed to create an audit log entry"""
    action: str
    device_mac: Optional[str] = None
    details: str


class AuditLogsResponse(BaseModel):
    """List of audit log entries"""
    audit_logs: list[AuditLog]
