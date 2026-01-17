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
    # If "public", look up the configured wallet from settings
    if wallet_id == "public":
        from .crud import get_wallet_id
        configured_wallet = await get_wallet_id()
        if not configured_wallet:
            return HTMLResponse(
                content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Flasher Not Configured</title>
                    <style>
                        body { font-family: sans-serif; background: #1d1d1d; color: white;
                               display: flex; justify-content: center; align-items: center;
                               min-height: 100vh; margin: 0; }
                        .container { text-align: center; padding: 2rem; }
                        h1 { color: #ff6b6b; }
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h1>Flasher Not Configured</h1>
                        <p>The admin needs to configure a wallet in the extension settings first.</p>
                    </div>
                </body>
                </html>
                """,
                status_code=503
            )
        wallet_id = configured_wallet

    # Read the standalone HTML template and replace wallet_id
    template_path = Path(__file__).parent / "templates" / "tnaflasher" / "public_page.html"
    html_content = template_path.read_text()
    # Replace Jinja2 variable with actual wallet_id
    html_content = html_content.replace("{{ wallet_id }}", wallet_id)
    return HTMLResponse(content=html_content)
