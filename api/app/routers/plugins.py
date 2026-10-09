"""Plugin del parco: catalogo da wordpress.org e plugin abbandonati."""
from fastapi import APIRouter, Depends

from .. import plugin_catalog
from ..auth import require_auth

router = APIRouter(prefix="/api/plugins", tags=["plugins"], dependencies=[Depends(require_auth)])


@router.get("/catalog")
async def catalog():
    return await plugin_catalog.overview()


@router.post("/catalog/scan")
async def catalog_scan():
    """Rinfresca subito tutto il catalogo (in sottofondo nel worker)."""
    from arq import create_pool
    from arq.connections import RedisSettings
    from ..config import settings
    pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    try:
        await pool.enqueue_job("plugin_catalog_scan", True, _job_id=f"plugcat:{int(__import__('time').time()) // 60}")
    finally:
        await pool.aclose()
    return {"queued": True}
