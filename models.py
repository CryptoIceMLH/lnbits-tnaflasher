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
