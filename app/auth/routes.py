import math
from contextlib import closing
from datetime import timedelta
from functools import lru_cache

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.utils import ahora

bp = Blueprint("auth", __name__)

MENSAJE_CREDENCIALES = "Credenciales incorrectas."


@lru_cache(maxsize=4)
def _hash_relleno(metodo):
    # Se compara contra él cuando el correo no existe, para que la respuesta
    # tarde lo mismo y no se pueda deducir qué correos están registrados.
    return generate_password_hash("relleno-no-valido", method=metodo)


def _respuesta_login(codigo):
    return render_template("auth/login.html"), codigo


@bp.route("/login", methods=["GET", "POST"])
def login():
    if "usuario_id" in session:
        return redirect(url_for("main.dashboard"))
    if request.method == "GET":
        return render_template("auth/login.html")

    cfg = current_app.config
    correo = request.form.get("correo", "").strip().lower()
    password = request.form.get("password", "")
    if not correo or not password:
        flash("Ingresa tu correo y tu contraseña.", "warning")
        return _respuesta_login(400)

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT u.ID_Usuario, u.Nombre_Completo, u.Contrasena_Hash,
                      u.Estado_Activo, u.Intentos_Fallidos, u.Fecha_Hora_Bloqueo,
                      u.FK_ID_Rol, r.Nombre_Rol
                 FROM usuario u
                 JOIN rol r ON r.ID_Rol = u.FK_ID_Rol
                WHERE u.Correo = %s""",
            (correo,),
        )
        usuario = cur.fetchone()

        # 1. Correo inexistente
        if usuario is None:
            check_password_hash(_hash_relleno(cfg["METODO_HASH"]), password)
            flash(MENSAJE_CREDENCIALES, "danger")
            return _respuesta_login(401)

        # 2. Cuenta deshabilitada (Estado_Activo)
        if not usuario["Estado_Activo"]:
            flash("Esta cuenta se encuentra deshabilitada. Contacte al administrador.", "danger")
            return _respuesta_login(403)

        # 3. Bloqueo temporal (Fecha_Hora_Bloqueo + MINUTOS_BLOQUEO)
        bloqueo = usuario["Fecha_Hora_Bloqueo"]
        if bloqueo is not None:
            fin_bloqueo = bloqueo + timedelta(minutes=cfg["MINUTOS_BLOQUEO"])
            if ahora() < fin_bloqueo:
                minutos = max(1, math.ceil((fin_bloqueo - ahora()).total_seconds() / 60))
                flash(f"Cuenta bloqueada temporalmente. Intenta nuevamente en {minutos} minuto(s).", "danger")
                return _respuesta_login(403)
            # El bloqueo ya venció: se reinicia el contador
            cur.execute(
                "UPDATE usuario SET Intentos_Fallidos = 0, Fecha_Hora_Bloqueo = NULL "
                "WHERE ID_Usuario = %s", (usuario["ID_Usuario"],))
            conn.commit()
            usuario["Intentos_Fallidos"] = 0

        # 4. Contraseña correcta
        if check_password_hash(usuario["Contrasena_Hash"], password):
            cur.execute(
                "UPDATE usuario SET Intentos_Fallidos = 0, Fecha_Hora_Bloqueo = NULL "
                "WHERE ID_Usuario = %s", (usuario["ID_Usuario"],))
            conn.commit()

            session.clear()  # evita reutilizar una sesión previa
            session.permanent = True
            session["usuario_id"] = usuario["ID_Usuario"]
            session["nombre"] = usuario["Nombre_Completo"]
            session["rol_id"] = usuario["FK_ID_Rol"]
            session["rol"] = usuario["Nombre_Rol"]
            return redirect(url_for("main.dashboard"))

        # 5. Contraseña incorrecta
        intentos = (usuario["Intentos_Fallidos"] or 0) + 1
        if intentos >= cfg["MAX_INTENTOS"]:
            cur.execute(
                "UPDATE usuario SET Intentos_Fallidos = %s, Fecha_Hora_Bloqueo = %s "
                "WHERE ID_Usuario = %s", (intentos, ahora(), usuario["ID_Usuario"]))
            flash(f"Demasiados intentos fallidos. Cuenta bloqueada por {cfg['MINUTOS_BLOQUEO']} minutos.", "danger")
        else:
            cur.execute(
                "UPDATE usuario SET Intentos_Fallidos = %s WHERE ID_Usuario = %s",
                (intentos, usuario["ID_Usuario"]))
            flash(MENSAJE_CREDENCIALES, "danger")
        conn.commit()
        return _respuesta_login(401)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Has cerrado sesión exitosamente.", "success")
    return redirect(url_for("auth.login"))
