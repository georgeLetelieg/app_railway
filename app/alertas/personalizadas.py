"""Alertas personalizadas (RF6): el usuario crea avisos a medida (título + fecha de vencimiento)
dentro del perfil de una maquinaria. Al llegar la fecha, el motor de alertas las notifica."""
from contextlib import closing

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from app import db
from app.alertas.routes import FECHA_MAX, FECHA_MIN, _leer_fecha
from app.maquinaria.routes import _obtener
from app.security import escritura_required, login_required

bp = Blueprint("personalizadas", __name__,
               url_prefix="/maquinaria/<int:maquinaria_id>/alertas/personalizadas")

TITULO_MIN, TITULO_MAX = 3, 255


def _obtener_alerta(cur, maquinaria_id, alerta_id):
    cur.execute("SELECT ID_Alerta_Personalizada, Titulo_Alerta, Fecha_Vencimiento, Estado_Activa "
                "FROM alerta_personalizada WHERE ID_Alerta_Personalizada = %s AND FK_ID_Maquinaria = %s",
                (alerta_id, maquinaria_id))
    alerta = cur.fetchone()
    if alerta is None:
        abort(404)
    return alerta


def _validar(cur, maquinaria_id, form, alerta_id=None):
    datos = {"titulo": form.get("titulo", ""), "fecha": form.get("fecha", "")}
    errores = []
    titulo = " ".join(datos["titulo"].split())          # recorta y colapsa espacios
    if not TITULO_MIN <= len(titulo) <= TITULO_MAX:
        errores.append(f"El título es obligatorio ({TITULO_MIN} a {TITULO_MAX} caracteres).")
    fecha = _leer_fecha(datos["fecha"], "vencimiento", errores) if datos["fecha"].strip() else None
    if fecha is None and not errores:
        errores.append("Indica la fecha de vencimiento.")
    if not errores:
        cur.execute("SELECT 1 AS x FROM alerta_personalizada WHERE FK_ID_Maquinaria = %s AND Titulo_Alerta = %s "
                    "AND Fecha_Vencimiento = %s AND ID_Alerta_Personalizada <> %s",
                    (maquinaria_id, titulo, fecha, alerta_id or 0))
        if cur.fetchone():
            errores.append("Ya existe una alerta con ese título y esa fecha para esta maquinaria.")
    return datos, titulo, fecha, errores


def _al_perfil(maquinaria_id):
    return redirect(url_for("maquinaria.perfil", maquinaria_id=maquinaria_id) + "#alertas-personalizadas")


def _formulario(maquina, alerta, datos, errores=(), codigo=200):
    for error in errores:
        flash(error, "danger")
    return render_template("alertas/personalizada_form.html", maquina=maquina, alerta=alerta, datos=datos,
                           fecha_min=FECHA_MIN.isoformat(), fecha_max=FECHA_MAX.isoformat()), codigo


@bp.route("/nueva", methods=["GET", "POST"])
@login_required
@escritura_required
def nueva(maquinaria_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        if request.method == "GET":
            return _formulario(maquina, None, {})
        datos, titulo, fecha, errores = _validar(cur, maquinaria_id, request.form)
        if errores:
            return _formulario(maquina, None, datos, errores, 400)
        cur.execute("INSERT INTO alerta_personalizada (FK_ID_Maquinaria, FK_ID_Usuario, Titulo_Alerta, "
                    "Fecha_Vencimiento, Estado_Activa) VALUES (%s, %s, %s, %s, 1)",
                    (maquinaria_id, session["usuario_id"], titulo, fecha))
        conn.commit()
    flash(f"Alerta «{titulo}» creada. Se notificará al acercarse el {fecha.strftime('%d-%m-%Y')}.", "success")
    return _al_perfil(maquinaria_id)


@bp.route("/<int:alerta_id>/editar", methods=["GET", "POST"])
@login_required
@escritura_required
def editar(maquinaria_id, alerta_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        alerta = _obtener_alerta(cur, maquinaria_id, alerta_id)
        if request.method == "GET":
            return _formulario(maquina, alerta, {"titulo": alerta["Titulo_Alerta"],
                                                 "fecha": alerta["Fecha_Vencimiento"].isoformat()})
        datos, titulo, fecha, errores = _validar(cur, maquinaria_id, request.form, alerta_id)
        if errores:
            return _formulario(maquina, alerta, datos, errores, 400)
        cur.execute("UPDATE alerta_personalizada SET Titulo_Alerta = %s, Fecha_Vencimiento = %s "
                    "WHERE ID_Alerta_Personalizada = %s AND FK_ID_Maquinaria = %s",
                    (titulo, fecha, alerta_id, maquinaria_id))
        conn.commit()
    flash("Alerta actualizada correctamente.", "success")
    return _al_perfil(maquinaria_id)


def _cambiar_estado(maquinaria_id, alerta_id, activa, mensaje):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        alerta = _obtener_alerta(cur, maquinaria_id, alerta_id)
        cur.execute("UPDATE alerta_personalizada SET Estado_Activa = %s "
                    "WHERE ID_Alerta_Personalizada = %s AND FK_ID_Maquinaria = %s",
                    (1 if activa else 0, alerta_id, maquinaria_id))
        conn.commit()
    flash(mensaje.format(titulo=alerta["Titulo_Alerta"]), "success")
    return _al_perfil(maquinaria_id)


@bp.route("/<int:alerta_id>/completar", methods=["POST"])
@login_required
@escritura_required
def completar(maquinaria_id, alerta_id):
    """Marca la alerta como cumplida: deja de notificarse pero se conserva en el historial."""
    return _cambiar_estado(maquinaria_id, alerta_id, False, "Alerta «{titulo}» marcada como cumplida.")


@bp.route("/<int:alerta_id>/reactivar", methods=["POST"])
@login_required
@escritura_required
def reactivar(maquinaria_id, alerta_id):
    return _cambiar_estado(maquinaria_id, alerta_id, True, "Alerta «{titulo}» reactivada.")


@bp.route("/<int:alerta_id>/eliminar", methods=["POST"])
@login_required
@escritura_required
def eliminar(maquinaria_id, alerta_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        alerta = _obtener_alerta(cur, maquinaria_id, alerta_id)
        cur.execute("DELETE FROM alerta_personalizada WHERE ID_Alerta_Personalizada = %s "
                    "AND FK_ID_Maquinaria = %s", (alerta_id, maquinaria_id))
        conn.commit()
    flash(f"Alerta «{alerta['Titulo_Alerta']}» eliminada.", "success")
    return _al_perfil(maquinaria_id)
