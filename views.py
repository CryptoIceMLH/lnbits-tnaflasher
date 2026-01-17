from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from lnbits.core.models import User
from lnbits.decorators import check_user_exists

from . import tnaflasher_renderer

tnaflasher_generic_router = APIRouter()


@tnaflasher_generic_router.get("/", response_class=HTMLResponse)
async def index(req: Request, user: User = Depends(check_user_exists)):
    """Admin dashboard page - requires LNbits login"""
    return tnaflasher_renderer().TemplateResponse(
        "index.html",
        {"request": req, "user": user.json()}
    )


@tnaflasher_generic_router.get("/{wallet_id}", response_class=HTMLResponse)
async def public_page(req: Request, wallet_id: str):
    """Public flasher page - no authentication required"""
    return tnaflasher_renderer().TemplateResponse(
        "public_page.html",
        {"request": req, "wallet_id": wallet_id}
    )
