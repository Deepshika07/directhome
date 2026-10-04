from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.errors import ApiError
from app.schemas.common import error_body

app = FastAPI(title="DirectHome API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000",
    "https://directhome-jet.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.message),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:])
    message = f"{field}: {first.get('msg')}" if field else first.get(
        "msg", "Invalid request"
    )
    return JSONResponse(
        status_code=422, content=error_body("VALIDATION_ERROR", message)
    )


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(api_router)
