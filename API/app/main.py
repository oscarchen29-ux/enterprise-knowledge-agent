from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.database import init_db
from app.docs import DESCRIPTION
from app.routers import auth, rewards_punishments, students, vehicle_passes

ROUTERS = [auth, students, rewards_punishments, vehicle_passes]


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="模擬校務行政系統 API",
    description=DESCRIPTION,
    openapi_tags=[module.TAG_METADATA for module in ROUTERS],
    lifespan=lifespan,
)

for module in ROUTERS:
    app.include_router(module.router)


@app.get("/", include_in_schema=False)
def redirect_to_docs():
    return RedirectResponse("/docs")
