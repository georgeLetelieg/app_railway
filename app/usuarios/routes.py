"""Gestión de usuarios (solo Super Administrador): listar, crear y cambiar el estado de las cuentas
(activar, inactivar y desbloquear). Todas las rutas están protegidas con @admin_required."""
from contextlib import closing
from datetime import timedelta

import pymysql
from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import generate_password_hash

from app import db
from app.auditoria.registro import ACCION_MODIFICACION, registrar
from app.security import admin_required
from app.usuarios import queries as q
from app.usuarios.validacion import (POLITICA_CONTRASENA, PASSWORD_MAX, PASSWORD_MIN, normalizar_correo,
                                     normalizar_nombre, validar_contrasena, validar_correo, validar_nombre)
from app.utils import ahora

bp = Blueprint("usuarios", __name__, url_prefix="/usuarios")

ESTADOS = ("activo", "inactivo", "bloqueado")
TEXTO_ESTADO = {"activo": "Activo", "inactivo": "Inactivo", "bloqueado": "Bloqueado"}
ACCIONES = ("activar", "inactivar", "desbloquear")

# Texto de ayuda al elegir un rol (coincide con los permisos definidos en config.py)
DESCRIPCION_ROLES = {
    "Super Administrador": "Acceso total: gestiona usuarios, corrige datos base y consulta la auditoría.",
    "Administrativo": "Registra y edita la información de la flota.",
    "Gerencia": "Solo consulta la información y la auditoría.",
    "Contador": "Solo consulta la información.",
}


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def estado_de(usuario):
    """'inactivo' > 'bloqueado' (bloqueo temporal vigente) > 'activo'."""
    if not usuario["Estado_Activo"]:
        return "inactivo"
    bloqueo = usuario["Fecha_Hora_Bloqueo"]
    if bloqueo is not None and ahora() < bloqueo + timedelta(minutes=current_app.config["MINUTOS_BLOQUEO"]):
        return "bloqueado"
    return "activo"


def _enriquecer(usuario):
    estado = estado_de(usuario)
    hasta = None
    if estado == "bloqueado":
        hasta = usuario["Fecha_Hora_Bloqueo"] + timedelta(minutes=current_app.config["MINUTOS_BLOQUEO"])
    return {**usuario, "estado": estado, "bloqueado_hasta": hasta,
            "es_yo": usuario["ID_Usuario"] == session.get("usuario_id")}


def _snapshot(usuario):
    """Lo que se guarda en la auditoría de un usuario. Nunca incluye la contraseña ni su hash."""
    return {"Usuario": usuario["Correo"], "Estado": TEXTO_ESTADO[estado_de(usuario)],
            "Intentos fallidos": str(usuario["Intentos_Fallidos"] or 0)}


def _formulario(roles, datos, errores=(), codigo=200):
    for error in errores:
        flash(error, "danger")
    return render_template("usuarios/form.html", roles=roles, datos=datos, descripciones=DESCRIPCION_ROLES,
                           politica=POLITICA_CONTRASENA, pw_min=PASSWORD_MIN, pw_max=PASSWORD_MAX), codigo


def _leer_formulario(cur, roles, form):
    """Valida el formulario de alta. Devuelve (datos para repoblar, valores limpios, errores).
    Las contraseñas nunca se devuelven para repoblar el formulario."""
    datos = {"nombre": form.get("nombre", ""), "correo": form.get("correo", ""), "rol": form.get("rol", "")}
    nombre = normalizar_nombre(datos["nombre"])
    correo = normalizar_correo(datos["correo"])
    password, confirmar = form.get("password", ""), form.get("confirmar", "")
    errores = []

    error_nombre, error_correo = validar_nombre(nombre), validar_correo(correo)
    errores += [e for e in (error_nombre, error_correo) if e]
    if not error_correo and q.existe_correo(cur, correo):
        errores.append("Ya existe un usuario con ese correo.")

    rol_id = int(datos["rol"]) if datos["rol"].isdigit() else None
    if rol_id not in {r["ID_Rol"] for r in roles}:
        errores.append("Selecciona un rol de la lista.")
        rol_id = None

    if not password:
        errores.append("La contraseña es obligatoria.")
    else:
        errores += validar_contrasena(password, correo, nombre)
        if password != confirmar:
            errores.append("Las contraseñas no coinciden.")
    return datos, {"nombre": nombre, "correo": correo, "rol_id": rol_id, "password": password}, errores


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
@bp.route("/")
@admin_required
def lista():
    busqueda = request.args.get("q", "").strip().lower()
    estado = request.args.get("estado", "")
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        usuarios = [_enriquecer(u) for u in q.listar_usuarios(cur)]

    conteo = {e: sum(1 for u in usuarios if u["estado"] == e) for e in ESTADOS}
    total = len(usuarios)
    if estado in ESTADOS:
        usuarios = [u for u in usuarios if u["estado"] == estado]
    if busqueda:
        usuarios = [u for u in usuarios if busqueda in u["Nombre_Completo"].lower() or busqueda in u["Correo"].lower()]
    return render_template("usuarios/lista.html", usuarios=usuarios, total=total, conteo=conteo,
                           busqueda=request.args.get("q", ""), estado=estado, estados=TEXTO_ESTADO)


@bp.route("/nuevo", methods=["GET", "POST"])
@admin_required
def nuevo():
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        roles = q.listar_roles(cur)
        if request.method == "GET":
            return _formulario(roles, {})

        datos, v, errores = _leer_formulario(cur, roles, request.form)
        if errores:
            return _formulario(roles, datos, errores, 400)

        # La contraseña se encripta (hash con sal) ANTES de llegar a la base de datos; nunca se guarda en claro.
        contrasena_hash = generate_password_hash(v["password"], method=current_app.config["METODO_HASH"])
        try:
            q.insertar_usuario(cur, v["nombre"], v["correo"], contrasena_hash, v["rol_id"])
            conn.commit()
        except pymysql.err.IntegrityError:      # otro administrador creó el mismo correo justo antes
            conn.rollback()
            return _formulario(roles, datos, ["Ya existe un usuario con ese correo."], 400)

    rol = next(r["Nombre_Rol"] for r in roles if r["ID_Rol"] == v["rol_id"])
    flash(f"Usuario {v['nombre']} creado con el rol {rol}. Ya puede iniciar sesión.", "success")
    return redirect(url_for("usuarios.lista"))


@bp.route("/<int:usuario_id>/estado", methods=["POST"])
@admin_required
def cambiar_estado(usuario_id):
    """accion = activar | inactivar | desbloquear. Cada cambio queda en la auditoría."""
    accion = request.form.get("accion", "")
    if accion not in ACCIONES:
        abort(400)

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        usuario = q.obtener_usuario(cur, usuario_id)
        if usuario is None:
            abort(404)
        if accion == "inactivar" and usuario_id == session["usuario_id"]:
            flash("No puedes inactivar tu propia cuenta: el sistema podría quedar sin administradores.", "danger")
            return redirect(url_for("usuarios.lista"))

        antes = _snapshot(usuario)
        nombre = usuario["Nombre_Completo"]
        if accion == "inactivar" and usuario["Estado_Activo"]:
            q.inactivar(cur, usuario_id)
            mensaje = f"Cuenta de {nombre} inactivada: ya no podrá iniciar sesión."
        elif accion == "activar" and not usuario["Estado_Activo"]:
            q.reactivar(cur, usuario_id)
            mensaje = f"Cuenta de {nombre} activada."
        elif accion == "desbloquear" and ((usuario["Intentos_Fallidos"] or 0) > 0 or usuario["Fecha_Hora_Bloqueo"] is not None):
            q.desbloquear(cur, usuario_id)
            mensaje = f"Cuenta de {nombre} desbloqueada."
        else:
            flash("No había nada que cambiar: la cuenta ya estaba en ese estado.", "info")
            return redirect(url_for("usuarios.lista"))

        despues = _snapshot(q.obtener_usuario(cur, usuario_id))
        if antes != despues:
            registrar(cur, "usuario", usuario_id, ACCION_MODIFICACION, antes, despues)
        conn.commit()

    flash(mensaje, "success")
    return redirect(url_for("usuarios.lista"))
