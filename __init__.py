import asyncio
from fastapi import APIRouter
from lnbits.db import Database
from lnbits.helpers import template_renderer
from lnbits.tasks import create_permanent_unique_task

db = Database("ext_tnaflasher")

tnaflasher_ext: APIRouter = APIRouter(prefix="/tnaflasher", tags=["TNA Flasher"])

tnaflasher_static_files = [
    {
        "path": "/tnaflasher/static",
        "name": "tnaflasher_static",
    }
]


def tnaflasher_renderer():
    return template_renderer(["tnaflasher/templates"])


from .views import tnaflasher_generic_router
from .views_api import tnaflasher_api_router

tnaflasher_ext.include_router(tnaflasher_generic_router)
tnaflasher_ext.include_router(tnaflasher_api_router)

scheduled_tasks: list[asyncio.Task] = []


def tnaflasher_start():
    from .tasks import wait_for_paid_invoices
    task = create_permanent_unique_task("ext_tnaflasher", wait_for_paid_invoices)
    scheduled_tasks.append(task)


def tnaflasher_stop():
    for task in scheduled_tasks:
        try:
            task.cancel()
        except Exception:
            pass


__all__ = [
    "db",
    "tnaflasher_ext",
    "tnaflasher_static_files",
    "tnaflasher_start",
    "tnaflasher_stop",
]
