"""Configuración central. Todos los valores sensibles vienen del archivo .env."""
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")  # no sobrescribe variables ya definidas por la plataforma


def _env(*nombres, defecto=None):
    """Primer valor no vacío entre varias variables de entorno."""
    for nombre in nombres:
        valor = os.environ.get(nombre)
        if valor not in (None, ""):
            return valor
    return defecto


# URL de conexión opcional (Railway: MYSQL_URL = mysql://usuario:clave@host:puerto/base)
_URL_BD = urlparse(_env("DATABASE_URL", "MYSQL_URL", defecto=""))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY")

    # Base de datos (MySQL 8.0.16+). Prioridad: DB_* > MYSQL* (Railway) > URL > valor local por defecto
    DB_HOST = _env("DB_HOST", "MYSQLHOST", defecto=_URL_BD.hostname or "localhost")
    DB_PORT = int(_env("DB_PORT", "MYSQLPORT", defecto=_URL_BD.port or 3306))
    DB_USER = _env("DB_USER", "MYSQLUSER", defecto=unquote(_URL_BD.username or "") or "root")
    DB_PASSWORD = _env("DB_PASSWORD", "MYSQLPASSWORD", defecto=unquote(_URL_BD.password or ""))
    DB_NAME = _env("DB_NAME", "MYSQLDATABASE", defecto=_URL_BD.path.lstrip("/") or "gestion_flota")

    # Detrás del proxy HTTPS de Railway (activa ProxyFix en app/__init__.py)
    DETRAS_DE_PROXY = os.environ.get("DETRAS_DE_PROXY", "0") == "1"

    # Sesión y cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)

    # Autenticación
    MAX_INTENTOS = 3
    MINUTOS_BLOQUEO = 15
    # "scrypt" (por defecto de Werkzeug 3). Si se exige SHA-256: "pbkdf2:sha256"
    METODO_HASH = "scrypt"

    # Archivos subidos (gestor documental): límite de 5 MB por archivo
    # En Railway apunta a un Volume (ej. /data/uploads); el disco del contenedor se borra en cada deploy
    UPLOAD_FOLDER = Path(_env("UPLOAD_FOLDER", defecto=str(BASE_DIR / "uploads")))
    # Límite por archivo: 5 MB (validado en app/archivos.py); la petición completa admite 6 MB
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024

    EMPRESA_NOMBRE = "Áridos y Maquinaria Fuentealba"

    # Alertas estáticas (RF5): umbrales de aviso
    ALERTA_DIAS_DOCUMENTOS = 30      # días antes del vencimiento de revisión técnica / permiso
    ALERTA_HORAS_MANTENCION = 50     # horas de uso restantes para la próxima mantención
    ALERTA_DIAS_PERSONALIZADAS = 7   # días de anticipación para alertas personalizadas (0 = solo el día del vencimiento)

    # Roles que pueden crear, editar y eliminar (el resto solo consulta)
    ROLES_ESCRITURA = ("Super Administrador", "Administrativo")

    # Único rol con acceso a la gestión de usuarios (crear, inactivar, desbloquear)
    ROL_SUPER_ADMIN = "Super Administrador"

    # Solo estos roles pueden corregir los datos base de una maquinaria (queda en la auditoría)
    ROLES_CORRECCION_BASE = ("Super Administrador",)
    # Roles que pueden consultar la auditoría de cambios y eliminaciones
    ROLES_AUDITORIA = ("Super Administrador", "Gerencia")

    ZONA_HORARIA = ZoneInfo("America/Santiago")
    DEBUG = os.environ.get("FLASK_DEBUG") == "1"
