from contextlib import closing
from functools import wraps

from flask import current_app, flash, redirect, session, url_for

from app import db


def login_required(f):
    """Exige sesión iniciada y que la cuenta siga activa en la base de datos."""
    @wraps(f)
    def decorated(*args, **kwargs):
        usuario_id = session.get("usuario_id")
        if usuario_id is None:
            flash("Por favor, inicia sesión para acceder al sistema.", "warning")
            return redirect(url_for("auth.login"))

        with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
            cur.execute("SELECT Estado_Activo FROM usuario WHERE ID_Usuario = %s", (usuario_id,))
            fila = cur.fetchone()

        if not fila or not fila["Estado_Activo"]:
            session.clear()
            flash("Tu cuenta ya no está habilitada.", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def roles_required(*roles):
    """Restringe una ruta a ciertos roles. Usar DESPUÉS de @login_required."""
    def decorador(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get("rol") not in roles:
                flash("No tienes permisos para acceder a esa sección.", "danger")
                return redirect(url_for("main.dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorador


def escritura_required(f):
    """Solo roles con permiso de escritura (config ROLES_ESCRITURA). Usar DESPUÉS de @login_required."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("rol") not in current_app.config["ROLES_ESCRITURA"]:
            flash("Tu rol solo permite consultar esta información.", "danger")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)
    return decorated


def config_roles_required(clave):
    """Restringe una ruta a los roles definidos en config[clave] (se lee en cada petición)."""
    def decorador(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get("rol") not in current_app.config[clave]:
                flash("No tienes permisos para acceder a esa sección.", "danger")
                return redirect(url_for("main.dashboard"))
            return f(*args, **kwargs)
        return decorated
    return decorador


def admin_required(f):
    """Solo el rol Super Administrador (config ROL_SUPER_ADMIN).

    A diferencia de los demás decoradores, no se fía solo de la sesión: en cada petición vuelve a consultar
    la base de datos, de modo que una cuenta desactivada, o a la que se le cambió el rol, pierde el acceso
    de inmediato. Incluye la comprobación de inicio de sesión, por lo que puede usarse solo.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        usuario_id = session.get("usuario_id")
        if usuario_id is None:
            flash("Por favor, inicia sesión para acceder al sistema.", "warning")
            return redirect(url_for("auth.login"))

        with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
            cur.execute("SELECT u.Estado_Activo, r.Nombre_Rol FROM usuario u "
                        "JOIN rol r ON r.ID_Rol = u.FK_ID_Rol WHERE u.ID_Usuario = %s", (usuario_id,))
            fila = cur.fetchone()

        if not fila or not fila["Estado_Activo"]:
            session.clear()
            flash("Tu cuenta ya no está habilitada.", "danger")
            return redirect(url_for("auth.login"))
        session["rol"] = fila["Nombre_Rol"]          # la base de datos manda sobre lo guardado en la sesión
        if fila["Nombre_Rol"] != current_app.config["ROL_SUPER_ADMIN"]:
            flash("Solo el Super Administrador puede acceder a la gestión de usuarios.", "danger")
            return redirect(url_for("main.dashboard"))
        return f(*args, **kwargs)
    return decorated
