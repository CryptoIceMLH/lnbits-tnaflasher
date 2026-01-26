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


class CreatePromoCode(BaseModel):
    """Data needed to create a promo code"""
    code: str
    discount_percent: int
    max_uses: int


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
    created_at: Optional[int] = None


class CreateMiner(BaseModel):
    """Data needed to create a miner"""
    name: str


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
    firmware: list[FirmwareInfo]
