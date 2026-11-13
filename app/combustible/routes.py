"""Módulo de combustible (RF4): cargas diarias por vehículo y cuadratura de litros y costos."""
import csv
from contextlib import closing
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from io import StringIO

from flask import (Blueprint, Response, abort, flash, redirect, render_template,
                   request, session, url_for)

from app import db
from app.auditoria.registro import ACCION_ELIMINACION, ACCION_MODIFICACION, registrar
from app.security import escritura_required, login_required
from app.utils import ahora, parse_numero_es

bp = Blueprint("combustible", __name__, url_prefix="/combustible")

POR_PAGINA = 50
LITROS_MAX_CARGA = Decimal("5000")          # tope anti-errores de digitación por carga
COSTO_MAX = Decimal("9999999999.99")
BASE_CARGAS = ("FROM carga_combustible c JOIN maquinaria m ON m.ID_Maquinaria = c.FK_ID_Maquinaria "
               "JOIN usuario u ON u.ID_Usuario = c.FK_ID_Usuario ")


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _parse_fecha(texto):
    try:
        return date.fromisoformat(texto)
    except (ValueError, TypeError):
        return None


def _leer_numero(texto, etiqueta, errores):
    try:
        valor = parse_numero_es(texto)
        if not valor.is_finite() or valor.copy_abs() > COSTO_MAX:
            raise ValueError
        return valor.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        errores.append(f"El campo {etiqueta} debe ser un número válido (usa coma para los decimales).")
        return None


def _decimal(valor):
    return Decimal(str(valor or 0))


def _por_litro(costo, litros):
    litros = _decimal(litros)
    return _decimal(costo) / litros if litros > 0 else None


def _filtros():
    """Filtros de la consulta. Sin parámetros: mes en curso. Con campos vacíos: sin límite."""
    hoy = ahora().date()
    if "desde" in request.args or "hasta" in request.args:
        desde, hasta = _parse_fecha(request.args.get("desde", "")), _parse_fecha(request.args.get("hasta", ""))
    else:
        desde, hasta = hoy.replace(day=1), hoy
    maquina = request.args.get("maquina", type=int)

    condiciones, params = ["1 = 1"], []
    if desde:
        condiciones.append("c.Fecha_Carga >= %s")
        params.append(datetime.combine(desde, time.min))
    if hasta:
        condiciones.append("c.Fecha_Carga < %s")
        params.append(datetime.combine(hasta + timedelta(days=1), time.min))
    if maquina:
        condiciones.append("c.FK_ID_Maquinaria = %s")
        params.append(maquina)
    contexto = {"desde": desde.isoformat() if desde else "", "hasta": hasta.isoformat() if hasta else "",
                "maquina": maquina}
    return " AND ".join(condiciones), params, contexto


def _maquinas(cur):
    cur.execute("SELECT ID_Maquinaria, Patente, Modelo, Tipo_Combustible FROM maquinaria ORDER BY Patente")
    return cur.fetchall()


def _validar(cur, form, carga_id=None):
    datos = {c: form.get(c, "") for c in ("maquina", "fecha_carga", "numero_vale", "litros", "costo_total")}
    errores = []

    maquina_id = int(datos["maquina"]) if datos["maquina"].isdigit() else None
    if maquina_id is not None:
        cur.execute("SELECT 1 AS x FROM maquinaria WHERE ID_Maquinaria = %s", (maquina_id,))
        if cur.fetchone() is None:
            maquina_id = None
    if maquina_id is None:
        errores.append("Selecciona una maquinaria válida.")

    try:
        fecha = datetime.fromisoformat(datos["fecha_carga"])
        if fecha.tzinfo is not None:
            raise ValueError
        if fecha > ahora() + timedelta(minutes=5):
            errores.append("La fecha de la carga no puede ser futura.")
    except ValueError:
        fecha = None
        errores.append("Indica una fecha y hora válidas.")

    vale = datos["numero_vale"].strip()
    if len(vale) > 50:
        errores.append("El número de vale no puede superar los 50 caracteres.")

    litros = _leer_numero(datos["litros"], "litros", errores)
    if litros is not None and not (0 < litros <= LITROS_MAX_CARGA):
        errores.append(f"Los litros deben ser mayores a 0 y no superar {LITROS_MAX_CARGA:.0f} por carga.")
        litros = None
    costo = _leer_numero(datos["costo_total"], "costo total", errores)
    if costo is not None and costo < 0:
        errores.append("El costo total no puede ser negativo.")
        costo = None

    if vale and maquina_id is not None and not errores:
        cur.execute("SELECT 1 AS x FROM carga_combustible WHERE FK_ID_Maquinaria = %s AND Numero_Vale = %s "
                    "AND ID_Carga <> %s", (maquina_id, vale, carga_id or 0))
        if cur.fetchone():
            errores.append(f"El vale N° {vale} ya está registrado para esa maquinaria.")

    valores = {"maquina": maquina_id, "fecha": fecha, "vale": vale or None, "litros": litros, "costo": costo}
    return datos, valores, errores


def _snapshot(patente, fecha, vale, litros, costo):
    """Datos de una carga tal como se guardan en la auditoría (texto legible)."""
    return {"Maquinaria": patente, "Fecha y hora": fecha.strftime("%d-%m-%Y %H:%M"), "N° de vale": vale or "",
            "Litros": f"{_decimal(litros):.2f}", "Costo total": f"{_decimal(costo):.2f}"}


def _obtener_carga(cur, carga_id):
    cur.execute("SELECT c.ID_Carga, c.FK_ID_Maquinaria, c.Fecha_Carga, c.Numero_Vale, c.Litros_Cargados, "
                "c.Costo_Total, m.Patente FROM carga_combustible c "
                "JOIN maquinaria m ON m.ID_Maquinaria = c.FK_ID_Maquinaria WHERE c.ID_Carga = %s", (carga_id,))
    carga = cur.fetchone()
    if carga is None:
        abort(404)
    return carga


def _formulario(cur, carga, datos, errores, codigo=200):
    for error in errores:
        flash(error, "danger")
    return render_template("combustible/form.html", carga=carga, datos=datos, maquinas=_maquinas(cur),
                           ahora=ahora().strftime("%Y-%m-%dT%H:%M")), codigo


def _destino(fecha, maquina_id):
    """Tras guardar, vuelve a la lista mostrando desde el día de la carga hasta hoy."""
    return url_for("combustible.lista", maquina=maquina_id, desde=fecha.date().isoformat(),
                   hasta=ahora().date().isoformat())


def _celda_csv(valor):
    """Evita inyección de fórmulas al abrir el CSV en Excel."""
    texto = "" if valor is None else str(valor)
    return "'" + texto if texto[:1] in ("=", "+", "-", "@", "\t", "\r") else texto


def _num_csv(valor, decimales=2):
    return f"{valor:.{decimales}f}".replace(".", ",") if valor is not None else ""


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def lista():
    donde, params, filtros = _filtros()
    pagina = max(1, request.args.get("pagina", 1, type=int))

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquinas = _maquinas(cur)
        cur.execute("SELECT m.ID_Maquinaria, m.Patente, m.Modelo, COUNT(*) AS cargas, "
                    "SUM(c.Litros_Cargados) AS litros, SUM(c.Costo_Total) AS costo "
                    f"{BASE_CARGAS}WHERE {donde} GROUP BY m.ID_Maquinaria, m.Patente, m.Modelo "
                    "ORDER BY m.Patente", params)
        resumen = cur.fetchall()
        cur.execute("SELECT c.ID_Carga, c.Fecha_Carga, c.Numero_Vale, c.Litros_Cargados, c.Costo_Total, "
                    "m.ID_Maquinaria, m.Patente, u.Nombre_Completo "
                    f"{BASE_CARGAS}WHERE {donde} ORDER BY c.Fecha_Carga DESC, c.ID_Carga DESC "
                    "LIMIT %s OFFSET %s", params + [POR_PAGINA, (pagina - 1) * POR_PAGINA])
        cargas = cur.fetchall()

    t_cargas = sum(f["cargas"] for f in resumen)
    t_litros = sum((_decimal(f["litros"]) for f in resumen), Decimal(0))
    t_costo = sum((_decimal(f["costo"]) for f in resumen), Decimal(0))
    totales = {"cargas": t_cargas, "litros": t_litros, "costo": t_costo,
               "por_litro": _por_litro(t_costo, t_litros)}
    resumen = [{**f, "por_litro": _por_litro(f["costo"], f["litros"])} for f in resumen]
    cargas = [{**c, "por_litro": _por_litro(c["Costo_Total"], c["Litros_Cargados"])} for c in cargas]

    paginas = max(1, -(-t_cargas // POR_PAGINA))
    return render_template("combustible/lista.html", maquinas=maquinas, resumen=resumen, cargas=cargas,
                           totales=totales, filtros=filtros, pagina=pagina, paginas=paginas)


@bp.route("/exportar.csv")
@login_required
def exportar():
    donde, params, _ = _filtros()
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        cur.execute("SELECT c.Fecha_Carga, m.Patente, m.Modelo, c.Numero_Vale, c.Litros_Cargados, "
                    f"c.Costo_Total, u.Nombre_Completo {BASE_CARGAS}WHERE {donde} "
                    "ORDER BY c.Fecha_Carga, c.ID_Carga", params)
        filas = cur.fetchall()

    salida = StringIO()
    escritor = csv.writer(salida, delimiter=";")
    escritor.writerow(["Fecha", "Patente", "Modelo", "Vale", "Litros", "Costo total", "Costo por litro",
                       "Registrado por"])
    for f in filas:
        escritor.writerow([f["Fecha_Carga"].strftime("%d-%m-%Y %H:%M"), _celda_csv(f["Patente"]),
                           _celda_csv(f["Modelo"]), _celda_csv(f["Numero_Vale"]),
                           _num_csv(_decimal(f["Litros_Cargados"])), _num_csv(_decimal(f["Costo_Total"])),
                           _num_csv(_por_litro(f["Costo_Total"], f["Litros_Cargados"])),
                           _celda_csv(f["Nombre_Completo"])])
    nombre = f"combustible_{ahora():%Y%m%d}.csv"
    return Response("\ufeff" + salida.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@bp.route("/nueva", methods=["GET", "POST"])
@login_required
@escritura_required
def nueva():
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        if request.method == "GET":
            return _formulario(cur, None, {"maquina": request.args.get("maquina", ""),
                                           "fecha_carga": ahora().strftime("%Y-%m-%dT%H:%M")}, [])
        datos, v, errores = _validar(cur, request.form)
        if errores:
            return _formulario(cur, None, datos, errores, 400)
        cur.execute(
            "INSERT INTO carga_combustible (FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Carga, Numero_Vale, "
            "Litros_Cargados, Costo_Total) VALUES (%s, %s, %s, %s, %s, %s)",
            (v["maquina"], session["usuario_id"], v["fecha"], v["vale"], v["litros"], v["costo"]))
        conn.commit()
    flash("Carga de combustible registrada.", "success")
    return redirect(_destino(v["fecha"], v["maquina"]))


@bp.route("/<int:carga_id>/editar", methods=["GET", "POST"])
@login_required
@escritura_required
def editar(carga_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        carga = _obtener_carga(cur, carga_id)
        if request.method == "GET":
            datos = {"maquina": str(carga["FK_ID_Maquinaria"]),
                     "fecha_carga": carga["Fecha_Carga"].strftime("%Y-%m-%dT%H:%M"),
                     "numero_vale": carga["Numero_Vale"] or "",
                     "litros": f"{_decimal(carga['Litros_Cargados']):.2f}".replace(".", ","),
                     "costo_total": f"{_decimal(carga['Costo_Total']):.2f}".replace(".", ",")}
            return _formulario(cur, carga, datos, [])
        datos, v, errores = _validar(cur, request.form, carga_id)
        if errores:
            return _formulario(cur, carga, datos, errores, 400)
        cur.execute("SELECT Patente FROM maquinaria WHERE ID_Maquinaria = %s", (v["maquina"],))
        patente_nueva = cur.fetchone()["Patente"]
        antes = _snapshot(carga["Patente"], carga["Fecha_Carga"], carga["Numero_Vale"],
                          carga["Litros_Cargados"], carga["Costo_Total"])
        despues = _snapshot(patente_nueva, v["fecha"], v["vale"], v["litros"], v["costo"])
        cur.execute(
            "UPDATE carga_combustible SET FK_ID_Maquinaria = %s, Fecha_Carga = %s, Numero_Vale = %s, "
            "Litros_Cargados = %s, Costo_Total = %s WHERE ID_Carga = %s",
            (v["maquina"], v["fecha"], v["vale"], v["litros"], v["costo"], carga_id))
        if antes != despues:                      # guardar sin cambiar nada no deja registro
            registrar(cur, "carga_combustible", carga_id, ACCION_MODIFICACION, antes, despues, v["maquina"])
        conn.commit()
    flash("Carga actualizada correctamente.", "success")
    return redirect(_destino(v["fecha"], v["maquina"]))


@bp.route("/<int:carga_id>/eliminar", methods=["POST"])
@login_required
@escritura_required
def eliminar(carga_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        carga = _obtener_carga(cur, carga_id)
        antes = _snapshot(carga["Patente"], carga["Fecha_Carga"], carga["Numero_Vale"],
                          carga["Litros_Cargados"], carga["Costo_Total"])
        cur.execute("DELETE FROM carga_combustible WHERE ID_Carga = %s", (carga_id,))
        registrar(cur, "carga_combustible", carga_id, ACCION_ELIMINACION, antes,
                  maquinaria_id=carga["FK_ID_Maquinaria"])
        conn.commit()
    flash("Carga de combustible eliminada.", "success")
    return redirect(url_for("combustible.lista", maquina=carga["FK_ID_Maquinaria"],
                            desde="", hasta=""))
