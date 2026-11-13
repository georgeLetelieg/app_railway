"""Historial y registro detallado de mantenciones (RF8).

Documenta intervenciones mecánicas, reparaciones y cambios de repuestos con fecha, lectura exacta del
horómetro (o kilometraje) al ingreso al taller y un texto libre amplio. Está separado de la bitácora
de uso diario. Al registrar una mantención preventiva se puede programar la próxima (RF5).
"""
from contextlib import closing
from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from app import db
from app.auditoria.registro import ACCION_ELIMINACION, ACCION_MODIFICACION, registrar
from app.alertas import motor
from app.alertas.routes import FECHA_MIN
from app.bitacora.routes import _leer_decimal
from app.maquinaria.routes import _obtener
from app.security import escritura_required, login_required
from app.utils import ahora, num_es

bp = Blueprint("mantenciones", __name__, url_prefix="/maquinaria/<int:maquinaria_id>/mantenciones")

POR_PAGINA = 20
DETALLE_MIN = 3
DETALLE_MAX_BYTES = 65535            # capacidad de la columna TEXT de MySQL (≈ 60.000 caracteres)
INTERVALO_MAX = Decimal("100000")


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _snapshot(fecha, lectura, detalle):
    """Datos de una mantención tal como se guardan en la auditoría (texto legible)."""
    return {"Fecha": fecha.strftime("%d-%m-%Y"), "Lectura": f"{Decimal(str(lectura)):.2f}", "Detalle": detalle}


def _obtener_registro(cur, maquinaria_id, registro_id):
    cur.execute("SELECT ID_Registro, Fecha_Mantencion, Horometro_Mecanico, Detalle_Trabajo "
                "FROM registro_mantencion WHERE ID_Registro = %s AND FK_ID_Maquinaria = %s",
                (registro_id, maquinaria_id))
    registro = cur.fetchone()
    if registro is None:
        abort(404)
    return registro


def _validar_secuencia(cur, maquinaria_id, fecha, lectura, registro_id, errores):
    """La lectura no puede retroceder respecto de mantenciones de fechas anteriores ni superar las posteriores."""
    for operador, orden, texto, retrocede in (("<", "DESC", "anterior", lambda a, b: a < b),
                                              (">", "ASC", "posterior", lambda a, b: a > b)):
        cur.execute(
            "SELECT Horometro_Mecanico FROM registro_mantencion WHERE FK_ID_Maquinaria = %s "
            f"AND Fecha_Mantencion {operador} %s AND ID_Registro <> %s "
            f"ORDER BY Horometro_Mecanico {orden} LIMIT 1", (maquinaria_id, fecha, registro_id or 0))
        fila = cur.fetchone()
        if fila is not None:
            otra = Decimal(str(fila["Horometro_Mecanico"]))
            if retrocede(lectura, otra):
                relacion = "menor" if texto == "anterior" else "mayor"
                errores.append(f"La lectura ({num_es(lectura)}) no puede ser {relacion} que la de la mantención "
                               f"{texto} ({num_es(otra)}); las lecturas no pueden retroceder.")


def _validar(cur, maquinaria_id, form, registro_id=None, con_intervalo=False):
    datos = {c: form.get(c, "") for c in ("fecha", "lectura", "detalle", "intervalo")}
    errores = []

    fecha = None
    try:
        fecha = date.fromisoformat(datos["fecha"])
        if fecha > ahora().date():
            errores.append("La fecha de la mantención no puede ser futura.")
        elif fecha < FECHA_MIN:
            errores.append("La fecha de la mantención no es válida.")
    except ValueError:
        errores.append("Indica una fecha válida.")

    lectura = _leer_decimal(datos["lectura"], "horómetro/kilometraje", errores)
    if lectura is None and not datos["lectura"].strip():
        errores.append("La lectura del horómetro o kilometraje al ingreso al taller es obligatoria.")

    detalle = datos["detalle"].replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(detalle) < DETALLE_MIN:
        errores.append("Describe el trabajo realizado.")
    elif len(detalle.encode("utf-8")) > DETALLE_MAX_BYTES:
        errores.append("El detalle es demasiado extenso (máximo cercano a 60.000 caracteres); "
                       "divídelo en dos registros.")

    intervalo = None
    if con_intervalo and datos["intervalo"].strip():
        intervalo = _leer_decimal(datos["intervalo"], "intervalo de mantención", errores)
        if intervalo is not None and not (0 < intervalo <= INTERVALO_MAX):
            errores.append(f"El intervalo debe ser mayor a 0 y no superar {INTERVALO_MAX:.0f}.")
            intervalo = None

    if fecha is not None and lectura is not None and not errores:
        _validar_secuencia(cur, maquinaria_id, fecha, lectura, registro_id, errores)
    return datos, {"fecha": fecha, "lectura": lectura, "detalle": detalle, "intervalo": intervalo}, errores


def _formulario(maquina, registro, datos, errores=(), codigo=200):
    for error in errores:
        flash(error, "danger")
    return render_template("mantenciones/form.html", maquina=maquina, registro=registro, datos=datos,
                           hoy=ahora().date().isoformat()), codigo


def _programar_proxima(cur, maquinaria_id, nuevo_id, fecha, lectura, intervalo):
    """Fija la próxima mantención preventiva (RF5) = lectura + intervalo, solo si esta es la mantención
    más reciente. Devuelve (meta o None, motivo si no se aplicó)."""
    cur.execute("SELECT 1 AS x FROM registro_mantencion WHERE FK_ID_Maquinaria = %s AND Fecha_Mantencion > %s "
                "AND ID_Registro <> %s LIMIT 1", (maquinaria_id, fecha, nuevo_id))
    if cur.fetchone():
        return None, "No se actualizó la próxima mantención preventiva porque existe una mantención más reciente."
    meta = (lectura + intervalo).quantize(Decimal("0.01"))
    cur.execute("SELECT 1 AS x FROM alerta_estatica WHERE FK_ID_Maquinaria = %s", (maquinaria_id,))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO alerta_estatica (FK_ID_Maquinaria) VALUES (%s)", (maquinaria_id,))
    cur.execute("UPDATE alerta_estatica SET Proxima_Mantencion_Preventiva = %s WHERE FK_ID_Maquinaria = %s",
                (meta, maquinaria_id))
    return meta, None


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def lista(maquinaria_id):
    q = request.args.get("q", "").strip()
    pagina = max(1, request.args.get("pagina", 1, type=int))
    donde, params = "r.FK_ID_Maquinaria = %s", [maquinaria_id]
    if q:
        donde += " AND r.Detalle_Trabajo LIKE %s"
        params.append(f"%{q}%")

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        cur.execute(f"SELECT COUNT(*) AS n FROM registro_mantencion r WHERE {donde}", params)
        total = cur.fetchone()["n"]
        cur.execute("SELECT r.ID_Registro, r.Fecha_Mantencion, r.Horometro_Mecanico, r.Detalle_Trabajo, "
                    "u.Nombre_Completo FROM registro_mantencion r JOIN usuario u ON u.ID_Usuario = r.FK_ID_Usuario "
                    f"WHERE {donde} ORDER BY r.Fecha_Mantencion DESC, r.ID_Registro DESC LIMIT %s OFFSET %s",
                    params + [POR_PAGINA, (pagina - 1) * POR_PAGINA])
        registros = cur.fetchall()
        alertas = motor.evaluar_una(cur, maquinaria_id)

    meta = next(i for i in alertas["items"] if i["clave"] == "mantencion")
    paginas = max(1, -(-total // POR_PAGINA))
    return render_template("mantenciones/lista.html", maquina=maquina, registros=registros, total=total, q=q,
                           pagina=pagina, paginas=paginas, meta=meta)


@bp.route("/nueva", methods=["GET", "POST"])
@login_required
@escritura_required
def nueva(maquinaria_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        if request.method == "GET":
            return _formulario(maquina, None, {"fecha": ahora().date().isoformat()})
        datos, v, errores = _validar(cur, maquinaria_id, request.form, con_intervalo=True)
        if errores:
            return _formulario(maquina, None, datos, errores, 400)

        cur.execute("INSERT INTO registro_mantencion (FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Mantencion, "
                    "Horometro_Mecanico, Detalle_Trabajo) VALUES (%s, %s, %s, %s, %s)",
                    (maquinaria_id, session["usuario_id"], v["fecha"], v["lectura"], v["detalle"]))
        nuevo_id = cur.lastrowid
        mensaje, aviso = "Mantención registrada correctamente.", None
        if v["intervalo"] is not None:
            meta, aviso = _programar_proxima(cur, maquinaria_id, nuevo_id, v["fecha"], v["lectura"], v["intervalo"])
            if meta is not None:
                mensaje += f" Próxima mantención preventiva programada a las {num_es(meta)}."
        conn.commit()

    flash(mensaje, "success")
    if aviso:
        flash(aviso, "warning")
    return redirect(url_for("mantenciones.lista", maquinaria_id=maquinaria_id))


@bp.route("/<int:registro_id>/editar", methods=["GET", "POST"])
@login_required
@escritura_required
def editar(maquinaria_id, registro_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        registro = _obtener_registro(cur, maquinaria_id, registro_id)
        if request.method == "GET":
            return _formulario(maquina, registro, {
                "fecha": registro["Fecha_Mantencion"].isoformat(),
                "lectura": f"{float(registro['Horometro_Mecanico']):.2f}".replace(".", ","),
                "detalle": registro["Detalle_Trabajo"]})
        datos, v, errores = _validar(cur, maquinaria_id, request.form, registro_id)
        if errores:
            return _formulario(maquina, registro, datos, errores, 400)
        antes = _snapshot(registro["Fecha_Mantencion"], registro["Horometro_Mecanico"], registro["Detalle_Trabajo"])
        despues = _snapshot(v["fecha"], v["lectura"], v["detalle"])
        cur.execute("UPDATE registro_mantencion SET Fecha_Mantencion = %s, Horometro_Mecanico = %s, "
                    "Detalle_Trabajo = %s WHERE ID_Registro = %s AND FK_ID_Maquinaria = %s",
                    (v["fecha"], v["lectura"], v["detalle"], registro_id, maquinaria_id))
        if antes != despues:                      # guardar sin cambiar nada no deja registro
            registrar(cur, "registro_mantencion", registro_id, ACCION_MODIFICACION, antes, despues, maquinaria_id)
        conn.commit()
    flash("Mantención actualizada correctamente.", "success")
    return redirect(url_for("mantenciones.lista", maquinaria_id=maquinaria_id))


@bp.route("/<int:registro_id>/eliminar", methods=["POST"])
@login_required
@escritura_required
def eliminar(maquinaria_id, registro_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        registro = _obtener_registro(cur, maquinaria_id, registro_id)
        antes = _snapshot(registro["Fecha_Mantencion"], registro["Horometro_Mecanico"], registro["Detalle_Trabajo"])
        cur.execute("DELETE FROM registro_mantencion WHERE ID_Registro = %s AND FK_ID_Maquinaria = %s",
                    (registro_id, maquinaria_id))
        registrar(cur, "registro_mantencion", registro_id, ACCION_ELIMINACION, antes, maquinaria_id=maquinaria_id)
        conn.commit()
    flash("Registro de mantención eliminado.", "success")
    return redirect(url_for("mantenciones.lista", maquinaria_id=maquinaria_id))
