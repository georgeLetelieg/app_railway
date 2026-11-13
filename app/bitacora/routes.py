"""Bitácora operativa (RF2): registro diario de horómetro/kilometraje y estado del día.

El historial es inalterable: NO existen rutas para editar ni eliminar registros
(la base de datos además lo impide con triggers, ver database/gestion_flota_v2.sql).
"""
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation

import pymysql
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app import db
from app.bitacora.queries import dias_sin_registro, ultimas_lecturas
from app.maquinaria.routes import _obtener
from app.security import escritura_required, login_required
from app.utils import ahora, parse_numero_es

bp = Blueprint("bitacora", __name__, url_prefix="/maquinaria/<int:maquinaria_id>/bitacora")

POR_PAGINA = 30
MAXIMO = Decimal("99999999.99")
SUGERENCIAS_ESTADO = ("Operativo", "Día en taller", "Sin actividad", "Fin de semana / feriado",
                      "Detenida por clima", "Sin operador")
CAMPOS = (("Horometro", "horómetro"), ("Kilometraje", "kilometraje"))


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _parse_fecha(texto):
    try:
        return date.fromisoformat(texto)
    except (ValueError, TypeError):
        return None


def _leer_decimal(texto, etiqueta, errores):
    if not (texto or "").strip():
        return None
    try:
        valor = parse_numero_es(texto)
    except InvalidOperation:
        errores.append(f"El {etiqueta} debe ser un número (usa coma para los decimales).")
        return None
    if not valor.is_finite() or valor < 0 or valor > MAXIMO:
        errores.append(f"El {etiqueta} debe estar entre 0 y {MAXIMO}.")
        return None
    return valor.quantize(Decimal("0.01"))


def _validar_secuencia(cur, maquinaria_id, fecha, campo, etiqueta, valor, errores):
    """Las lecturas no pueden retroceder: deben ser >= a la anterior y <= a la posterior."""
    if valor is None:
        return
    for operador, orden, texto, comparar in (
            ("<", "DESC", "anterior", lambda v, o: v < o),
            (">", "ASC", "posterior", lambda v, o: v > o)):
        cur.execute(
            f"SELECT {campo} FROM bitacora_operativa WHERE FK_ID_Maquinaria = %s "
            f"AND Fecha_Registro {operador} %s AND {campo} IS NOT NULL "
            f"ORDER BY {campo} {orden} LIMIT 1", (maquinaria_id, fecha))
        fila = cur.fetchone()
        if fila is not None:
            otro = Decimal(str(fila[campo]))
            if comparar(valor, otro):
                relacion = "menor" if texto == "anterior" else "mayor"
                errores.append(f"El {etiqueta} ({valor:.2f}) no puede ser {relacion} que el registro "
                               f"{texto} ({otro:.2f}); las lecturas no pueden retroceder.")


def _formulario(maquina, datos, lecturas, errores, codigo=200):
    for error in errores:
        flash(error, "danger")
    return render_template("bitacora/form.html", maquina=maquina, datos=datos, lecturas=lecturas,
                           hoy=ahora().date().isoformat(), sugerencias=SUGERENCIAS_ESTADO), codigo


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def lista(maquinaria_id):
    desde = _parse_fecha(request.args.get("desde", ""))
    hasta = _parse_fecha(request.args.get("hasta", ""))
    pagina = max(1, request.args.get("pagina", 1, type=int))

    condiciones, params = ["b.FK_ID_Maquinaria = %s"], [maquinaria_id]
    if desde:
        condiciones.append("b.Fecha_Registro >= %s")
        params.append(desde)
    if hasta:
        condiciones.append("b.Fecha_Registro <= %s")
        params.append(hasta)
    donde = " AND ".join(condiciones)

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        cur.execute(f"SELECT COUNT(*) AS n FROM bitacora_operativa b WHERE {donde}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            "SELECT b.ID_Bitacora, b.Fecha_Registro, b.Fecha_Hora_Ingreso, b.Horometro, b.Kilometraje, "
            "b.Estado_Diario, u.Nombre_Completo "
            "FROM bitacora_operativa b JOIN usuario u ON u.ID_Usuario = b.FK_ID_Usuario "
            f"WHERE {donde} ORDER BY b.Fecha_Registro DESC LIMIT %s OFFSET %s",
            params + [POR_PAGINA, (pagina - 1) * POR_PAGINA])
        registros = cur.fetchall()
        lecturas = ultimas_lecturas(cur, maquinaria_id)
        faltantes = dias_sin_registro(cur, maquinaria_id, ahora().date())

    paginas = max(1, -(-total // POR_PAGINA))
    return render_template("bitacora/lista.html", maquina=maquina, registros=registros, total=total,
                           lecturas=lecturas, faltantes=faltantes, pagina=pagina, paginas=paginas,
                           desde=desde.isoformat() if desde else "", hasta=hasta.isoformat() if hasta else "")


@bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@escritura_required
def nuevo(maquinaria_id):
    hoy = ahora().date()
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        lecturas = ultimas_lecturas(cur, maquinaria_id)

        if request.method == "GET":
            fecha = _parse_fecha(request.args.get("fecha", "")) or hoy
            return _formulario(maquina, {"fecha": min(fecha, hoy).isoformat()}, lecturas, [])

        form = request.form
        datos = {"fecha": form.get("fecha", ""), "horometro": form.get("horometro", ""),
                 "kilometraje": form.get("kilometraje", ""), "estado_diario": form.get("estado_diario", "")}
        errores = []

        fecha = _parse_fecha(datos["fecha"])
        if fecha is None:
            errores.append("Indica una fecha válida.")
        elif fecha > hoy:
            errores.append("La fecha del registro no puede ser futura.")
        horometro = _leer_decimal(datos["horometro"], "horómetro", errores)
        kilometraje = _leer_decimal(datos["kilometraje"], "kilometraje", errores)
        estado = datos["estado_diario"].strip()
        if not estado or len(estado) > 255:
            errores.append("El estado del día es obligatorio (máximo 255 caracteres).")

        if not errores:
            cur.execute("SELECT 1 AS x FROM bitacora_operativa WHERE FK_ID_Maquinaria = %s "
                        "AND Fecha_Registro = %s", (maquinaria_id, fecha))
            if cur.fetchone():
                errores.append("Ya existe un registro para esa fecha. La bitácora es inalterable: "
                               "no admite modificaciones ni segundos registros del mismo día.")
        if not errores:
            for (campo, etiqueta), valor in zip(CAMPOS, (horometro, kilometraje)):
                _validar_secuencia(cur, maquinaria_id, fecha, campo, etiqueta, valor, errores)
        if errores:
            return _formulario(maquina, datos, lecturas, errores, 400)

        try:
            cur.execute(
                "INSERT INTO bitacora_operativa (FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Registro, "
                "Fecha_Hora_Ingreso, Horometro, Kilometraje, Estado_Diario) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (maquinaria_id, session["usuario_id"], fecha, ahora(), horometro, kilometraje, estado))
            conn.commit()
        except pymysql.err.IntegrityError:
            conn.rollback()
            return _formulario(maquina, datos, lecturas,
                               ["Ya existe un registro para esa fecha."], 400)

    flash(f"Registro del {fecha.strftime('%d-%m-%Y')} guardado. Ya no podrá modificarse.", "success")
    return redirect(url_for("bitacora.lista", maquinaria_id=maquinaria_id))
