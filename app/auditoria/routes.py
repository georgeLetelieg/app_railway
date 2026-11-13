"""Consulta de la auditoría: correcciones y eliminaciones de datos (solo lectura)."""
import json
from contextlib import closing
from datetime import date, datetime, time, timedelta

from flask import Blueprint, render_template, request

from app import db
from app.auditoria.registro import TABLAS, cambios
from app.security import config_roles_required, login_required

bp = Blueprint("auditoria", __name__, url_prefix="/auditoria")
POR_PAGINA = 30


def _parse_fecha(texto):
    try:
        return date.fromisoformat(texto)
    except (ValueError, TypeError):
        return None


@bp.route("/")
@login_required
@config_roles_required("ROLES_AUDITORIA")
def lista():
    tabla = request.args.get("tabla", "")
    maquina = request.args.get("maquina", type=int)
    desde, hasta = _parse_fecha(request.args.get("desde", "")), _parse_fecha(request.args.get("hasta", ""))
    pagina = max(1, request.args.get("pagina", 1, type=int))

    condiciones, params = ["1 = 1"], []
    if tabla in TABLAS:
        condiciones.append("a.Tabla_Afectada = %s")
        params.append(tabla)
    if maquina:
        condiciones.append("a.FK_ID_Maquinaria = %s")
        params.append(maquina)
    if desde:
        condiciones.append("a.Fecha_Hora >= %s")
        params.append(datetime.combine(desde, time.min))
    if hasta:
        condiciones.append("a.Fecha_Hora < %s")
        params.append(datetime.combine(hasta + timedelta(days=1), time.min))
    donde = " AND ".join(condiciones)

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        cur.execute("SELECT ID_Maquinaria, Patente FROM maquinaria ORDER BY Patente")
        maquinas = cur.fetchall()
        cur.execute(f"SELECT COUNT(*) AS n FROM auditoria a WHERE {donde}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            "SELECT a.ID_Auditoria, a.Tabla_Afectada, a.ID_Registro, a.Accion, a.Fecha_Hora, a.Datos_Anteriores, "
            "a.Datos_Nuevos, u.Nombre_Completo, m.Patente FROM auditoria a "
            "JOIN usuario u ON u.ID_Usuario = a.FK_ID_Usuario "
            "LEFT JOIN maquinaria m ON m.ID_Maquinaria = a.FK_ID_Maquinaria "
            f"WHERE {donde} ORDER BY a.Fecha_Hora DESC, a.ID_Auditoria DESC LIMIT %s OFFSET %s",
            params + [POR_PAGINA, (pagina - 1) * POR_PAGINA])
        filas = cur.fetchall()

    registros = []
    for f in filas:
        antes = json.loads(f["Datos_Anteriores"])
        despues = json.loads(f["Datos_Nuevos"]) if f["Datos_Nuevos"] else None
        registros.append({**f, "tabla_texto": TABLAS.get(f["Tabla_Afectada"], f["Tabla_Afectada"]),
                          # Identifica el registro afectado aunque ese dato no haya cambiado (p. ej. el correo del usuario)
                          "referencia": f["Patente"] or antes.get("Usuario"),
                          "cambios": cambios(antes, despues)})
    paginas = max(1, -(-total // POR_PAGINA))
    filtros = {"tabla": tabla, "maquina": maquina, "desde": desde.isoformat() if desde else "",
               "hasta": hasta.isoformat() if hasta else ""}
    return render_template("auditoria/lista.html", registros=registros, total=total, maquinas=maquinas,
                           tablas=TABLAS, filtros=filtros, pagina=pagina, paginas=paginas)
