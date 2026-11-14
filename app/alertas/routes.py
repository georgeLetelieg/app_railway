"""Alertas estáticas (RF5): centro de alertas y edición de los vencimientos obligatorios."""
from contextlib import closing
from datetime import date
from decimal import Decimal

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app import db
from app.alertas import motor
from app.bitacora.queries import ultimas_lecturas
from app.bitacora.routes import _leer_decimal
from app.maquinaria.routes import _obtener
from app.security import escritura_required, login_required

bp = Blueprint("alertas", __name__)

FECHA_MIN, FECHA_MAX = date(2000, 1, 1), date(2100, 12, 31)


def _leer_fecha(texto, etiqueta, errores):
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        valor = date.fromisoformat(texto)
        if not FECHA_MIN <= valor <= FECHA_MAX:
            raise ValueError
        return valor
    except ValueError:
        errores.append(f"La fecha de {etiqueta} no es válida (debe estar entre 2000 y 2100).")
        return None


@bp.route("/alertas/")
@login_required
def centro():
    flota = motor.flota_alertas()
    return render_template("alertas/centro.html", alertas=motor.alertas_ordenadas(flota, incluir_sin_dato=True),
                           resumen=motor.resumen(flota))


@bp.route("/maquinaria/<int:maquinaria_id>/alertas/editar", methods=["GET", "POST"])
@login_required
@escritura_required
def editar(maquinaria_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        cur.execute("SELECT Vencimiento_Revision_Tecnica, Vencimiento_Permiso_Circulacion, "
                    "Proxima_Mantencion_Preventiva FROM alerta_estatica WHERE FK_ID_Maquinaria = %s",
                    (maquinaria_id,))
        actual = cur.fetchone()
        lecturas = ultimas_lecturas(cur, maquinaria_id)

        if request.method == "GET":
            actual = actual or {}
            datos = {"revision": actual.get("Vencimiento_Revision_Tecnica"),
                     "permiso": actual.get("Vencimiento_Permiso_Circulacion"),
                     "mantencion": actual.get("Proxima_Mantencion_Preventiva")}
            datos = {"revision": datos["revision"].isoformat() if datos["revision"] else "",
                     "permiso": datos["permiso"].isoformat() if datos["permiso"] else "",
                     "mantencion": f"{float(datos['mantencion']):.2f}".replace(".", ",") if datos["mantencion"] is not None else ""}
            return render_template("alertas/form.html", maquina=maquina, datos=datos, lecturas=lecturas)

        datos = {"revision": request.form.get("revision", ""), "permiso": request.form.get("permiso", ""),
                 "mantencion": request.form.get("mantencion", "")}
        errores = []
        revision = _leer_fecha(datos["revision"], "la revisión técnica", errores)
        permiso = _leer_fecha(datos["permiso"], "el permiso de circulación", errores)
        mantencion = _leer_decimal(datos["mantencion"], "horómetro de la próxima mantención", errores)
        if errores:
            for error in errores:
                flash(error, "danger")
            return render_template("alertas/form.html", maquina=maquina, datos=datos, lecturas=lecturas), 400

        if actual is None:   # por si la fila 1 a 1 no existiera todavía
            cur.execute("INSERT INTO alerta_estatica (FK_ID_Maquinaria) VALUES (%s)", (maquinaria_id,))
        cur.execute("UPDATE alerta_estatica SET Vencimiento_Revision_Tecnica = %s, "
                    "Vencimiento_Permiso_Circulacion = %s, Proxima_Mantencion_Preventiva = %s "
                    "WHERE FK_ID_Maquinaria = %s", (revision, permiso, mantencion, maquinaria_id))
        anterior = (actual or {}).get("Proxima_Mantencion_Preventiva")
        anterior = None if anterior is None else Decimal(str(anterior)).quantize(Decimal("0.01"))
        if anterior != mantencion:
            # Meta fijada a mano: deja de depender de la mantención que la había programado
            cur.execute("UPDATE alerta_estatica SET FK_ID_Mantencion_Origen = NULL, Intervalo_Mantencion = NULL "
                        "WHERE FK_ID_Maquinaria = %s", (maquinaria_id,))
        conn.commit()

    flash("Vencimientos actualizados correctamente.", "success")
    return redirect(url_for("maquinaria.perfil", maquinaria_id=maquinaria_id))
