from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """Erreur métier, transformée en réponse JSON par le handler plus bas."""

    def __init__(self, status_code: int, code: str, message: str, details=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def unauthorized(message: str = "Invalid or expired credentials") -> AppError:
    return AppError(401, "unauthorized", message)


def forbidden(message: str = "You do not have permission to perform this action") -> AppError:
    return AppError(403, "forbidden", message)


def not_found(entity: str = "Resource") -> AppError:
    return AppError(404, "not_found", f"{entity} not found")


def conflict(message: str, details=None) -> AppError:
    return AppError(409, "conflict", message, details)


def _body(code: str, message: str, details=None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details)
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=_body("validation_error", "Invalid request data", jsonable_encoder(exc.errors())),
        )

    @app.exception_handler(DataError)
    async def handle_data_error(request: Request, exc: DataError):
        # Classe SQLSTATE 22 : valeur refusée par PostgreSQL (id hors des limites d'un
        # integer, caractère NUL dans un texte, nombre trop grand...). Elle vient toujours
        # d'une donnée envoyée par le client -> 422 et non 500. Le détail SQL n'est pas renvoyé.
        return JSONResponse(
            status_code=422,
            content=_body("invalid_value", "A value in the request is out of range or invalid"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=_body("http_error", str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )