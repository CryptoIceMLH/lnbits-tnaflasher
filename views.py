from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from lnbits.core.models import User
from lnbits.decorators import check_user_exists
from pathlib import Path

from . import tnaflasher_renderer

tnaflasher_generic_router = APIRouter()


@tnaflasher_generic_router.get("/", response_class=HTMLResponse)
async def index(req: Request, user: User = Depends(check_user_exists)):
    """Admin dashboard page - requires LNbits login"""
    return tnaflasher_renderer().TemplateResponse(
        "tnaflasher/index.html",
        {"request": req, "user": user.json()}
    )


@tnaflasher_generic_router.get("/{wallet_id}", response_class=HTMLResponse)
async def public_page(req: Request, wallet_id: str):
    """Public flasher page - no authentication required"""
    # Read the standalone HTML template and replace wallet_id
    template_path = Path(__file__).parent / "templates" / "tnaflasher" / "public_page.html"
    html_content = template_path.read_text()
    # Replace Jinja2 variable with actual wallet_id
    html_content = html_content.replace("{{ wallet_id }}", wallet_id)
    return HTMLResponse(content=html_content)
