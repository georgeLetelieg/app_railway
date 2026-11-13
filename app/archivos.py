"""Utilidades comunes para archivos subidos (fotos y documentos)."""
from decimal import ROUND_UP, Decimal
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import current_app
from PIL import Image, UnidentifiedImageError

MAX_BYTES = 5 * 1024 * 1024  # 5 MB por archivo (igual que el CHECK de la tabla documento)
TIPOS = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
_FORMATOS_IMAGEN = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
MENSAJE_TIPO = "El archivo debe ser un PDF o una imagen (JPG, PNG o WEBP) válida."


def leer_subida(archivo):
    """Lee el archivo subido (FileStorage) validando que no esté vacío ni supere 5 MB."""
    contenido = archivo.stream.read(MAX_BYTES + 1)
    if not contenido:
        raise ValueError("El archivo está vacío.")
    if len(contenido) > MAX_BYTES:
        raise ValueError("El archivo supera el límite de 5 MB.")
    return contenido


def detectar_tipo(contenido):
    """Devuelve la extensión ('pdf', 'jpg', 'png', 'webp') según el CONTENIDO real del archivo."""
    if b"%PDF-" in contenido[:1024]:
        return "pdf"
    try:
        imagen = Image.open(BytesIO(contenido))
        extension = _FORMATOS_IMAGEN.get(imagen.format)
        if extension is None:
            raise ValueError(MENSAJE_TIPO)
        imagen.verify()
        return extension
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError(MENSAJE_TIPO)


def tamano_mb(contenido):
    """Tamaño en MB redondeado hacia arriba (mínimo 0,01) para cumplir CHECK > 0 de la base."""
    return (Decimal(len(contenido)) / Decimal(1024 * 1024)).quantize(Decimal("0.01"), rounding=ROUND_UP)


def guardar_archivo(subcarpeta, contenido, extension):
    """Guarda con nombre aleatorio en uploads/<subcarpeta>/ y devuelve la ruta relativa."""
    ruta = f"{subcarpeta}/{uuid4().hex}.{extension}"
    destino = Path(current_app.config["UPLOAD_FOLDER"]) / ruta
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(contenido)
    return ruta


def borrar_archivo(ruta):
    if not ruta:
        return
    base = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    destino = (base / ruta).resolve()
    if base in destino.parents:
        destino.unlink(missing_ok=True)
