from lookout.adapters.web.routes.auth import router as auth_router
from lookout.adapters.web.routes.cameras import router as cameras_router
from lookout.adapters.web.routes.entities import router as entities_router
from lookout.adapters.web.routes.events import router as events_router
from lookout.adapters.web.routes.settings import router as settings_router
from lookout.adapters.web.routes.setup import router as setup_router

__all__ = [
    "auth_router",
    "cameras_router",
    "entities_router",
    "events_router",
    "settings_router",
    "setup_router",
]
