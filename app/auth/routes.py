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


def _ip_cliente():
    # Con DETRAS_DE_PROXY=1 (Railway), ProxyFix deja aquí la IP real del cliente y no la del proxy
    return (request.remote_addr or "desconocida")[:45]


def _registrar_fallo_ip(cur, ip, momento):
    cur.execute("INSERT INTO intento_login (IP, Fecha_Hora) VALUES (%s, %s)", (ip, momento))
    cur.execute("DELETE FROM intento_login WHERE Fecha_Hora < %s", (momento - timedelta(days=1),))


def _rechazar(conn, cur, ip, momento):
    """Respuesta única para cualquier fallo: no revela si el correo existe ni el estado de la cuenta."""
    _registrar_fallo_ip(cur, ip, momento)
    conn.commit()
    flash(MENSAJE_CREDENCIALES, "danger")
    return _respuesta_login(401)


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

    ip, momento = _ip_cliente(), ahora()
    ventana = timedelta(minutes=cfg["MINUTOS_BLOQUEO"])
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        # 0. Límite por IP: frena el ataque de una misma contraseña contra muchas cuentas
        cur.execute("SELECT COUNT(*) AS n FROM intento_login WHERE IP = %s AND Fecha_Hora >= %s",
                    (ip, momento - ventana))
        if cur.fetchone()["n"] >= cfg["MAX_INTENTOS_IP"]:
            flash(f"Demasiados intentos fallidos desde esta conexión. Espera {cfg['MINUTOS_BLOQUEO']} minutos "
                  "e inténtalo de nuevo.", "danger")
            return _respuesta_login(429)

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

        # 1. Correo inexistente: se calcula igual un hash para que la respuesta tarde lo mismo
        if usuario is None:
            check_password_hash(_hash_relleno(cfg["METODO_HASH"]), password)
            return _rechazar(conn, cur, ip, momento)
        uid = usuario["ID_Usuario"]

        # 2. Bloqueo temporal vencido: se reinicia el contador
        fin_bloqueo = None
        if usuario["Fecha_Hora_Bloqueo"] is not None:
            fin_bloqueo = usuario["Fecha_Hora_Bloqueo"] + ventana
            if momento >= fin_bloqueo:
                cur.execute("UPDATE usuario SET Intentos_Fallidos = 0, Fecha_Hora_Bloqueo = NULL "
                            "WHERE ID_Usuario = %s", (uid,))
                conn.commit()
                fin_bloqueo = None

        # 3. Cuenta deshabilitada o bloqueada: el motivo solo se muestra si la contraseña es correcta
        if not usuario["Estado_Activo"] or fin_bloqueo is not None:
            if not check_password_hash(usuario["Contrasena_Hash"], password):
                return _rechazar(conn, cur, ip, momento)
            if not usuario["Estado_Activo"]:
                flash("Esta cuenta se encuentra deshabilitada. Contacte al administrador.", "danger")
            else:
                minutos = max(1, math.ceil((fin_bloqueo - momento).total_seconds() / 60))
                flash(f"Cuenta bloqueada temporalmente por intentos fallidos. Intenta nuevamente en "
                      f"{minutos} minuto(s).", "danger")
            return _respuesta_login(403)

        # 4. Se reserva el intento ANTES de verificar la clave, con un incremento atómico en la base:
        #    peticiones simultáneas no pueden probar más de MAX_INTENTOS contraseñas.
        cur.execute("UPDATE usuario SET Intentos_Fallidos = Intentos_Fallidos + 1 WHERE ID_Usuario = %s", (uid,))
        conn.commit()
        cur.execute("SELECT Intentos_Fallidos FROM usuario WHERE ID_Usuario = %s", (uid,))
        intentos = cur.fetchone()["Intentos_Fallidos"]

        if intentos <= cfg["MAX_INTENTOS"] and check_password_hash(usuario["Contrasena_Hash"], password):
            # 5. Contraseña correcta
            cur.execute("UPDATE usuario SET Intentos_Fallidos = 0, Fecha_Hora_Bloqueo = NULL "
                        "WHERE ID_Usuario = %s", (uid,))
            conn.commit()
            session.clear()  # evita reutilizar una sesión previa
            session.permanent = True
            session["usuario_id"] = uid
            session["nombre"] = usuario["Nombre_Completo"]
            session["rol_id"] = usuario["FK_ID_Rol"]
            session["rol"] = usuario["Nombre_Rol"]
            return redirect(url_for("main.dashboard"))

        # 6. Contraseña incorrecta (o intentos agotados por peticiones simultáneas)
        if intentos >= cfg["MAX_INTENTOS"]:
            cur.execute("UPDATE usuario SET Fecha_Hora_Bloqueo = %s "
                        "WHERE ID_Usuario = %s AND Fecha_Hora_Bloqueo IS NULL", (momento, uid))
        return _rechazar(conn, cur, ip, momento)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Has cerrado sesión exitosamente.", "success")
    return redirect(url_for("auth.login"))
