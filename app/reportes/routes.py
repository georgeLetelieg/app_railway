"""Exportación y reportabilidad (RF7): ficha técnica de la maquinaria en PDF, lista para imprimir.
Incluye datos base, alertas, historial operativo (bitácora) e historial de mantenciones."""
import re
from contextlib import closing
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from flask import Blueprint, current_app, request, send_file, session

from app import db
from app.alertas import motor
from app.bitacora.queries import ultimas_lecturas
from app.maquinaria.routes import _obtener
from app.reportes.ficha import generar_ficha
from app.security import login_required
from app.utils import ahora

bp = Blueprint("reportes", __name__)

PERIODOS = {"30": 30, "90": 90, "180": 180, "365": 365, "todo": None}
PERIODO_POR_DEFECTO = "90"
MAX_FILAS_FICHA = 1500
MAX_MANTENCIONES_FICHA = 100


def _ruta_foto(ruta):
    """Ruta absoluta de la fotografía si existe dentro de uploads/, o None."""
    if not ruta:
        return None
    base = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    destino = (base / ruta).resolve()
    return destino if base in destino.parents and destino.is_file() else None


@bp.route("/maquinaria/<int:maquinaria_id>/ficha.pdf")
@login_required
def ficha(maquinaria_id):
    """Ficha técnica en PDF. ?periodo=30|90|180|365|todo  ·  ?descargar=1 fuerza la descarga."""
    periodo = request.args.get("periodo", PERIODO_POR_DEFECTO)
    if periodo not in PERIODOS:
        periodo = PERIODO_POR_DEFECTO
    descargar = request.args.get("descargar") == "1"
    ahora_ = ahora()
    dias = PERIODOS[periodo]
    desde = ahora_.date() - timedelta(days=dias) if dias else None

    sql = ("SELECT b.Fecha_Registro, b.Horometro, b.Kilometraje, b.Estado_Diario, b.Horas_Trabajadas, "
           "b.Faena, b.Operador, b.Numero_Parte, u.Nombre_Completo "
           "FROM bitacora_operativa b JOIN usuario u ON u.ID_Usuario = b.FK_ID_Usuario "
           "WHERE b.FK_ID_Maquinaria = %s")
    params = [maquinaria_id]
    if desde:
        sql += " AND b.Fecha_Registro >= %s"
        params.append(desde)
    sql += " ORDER BY b.Fecha_Registro DESC LIMIT %s"
    params.append(MAX_FILAS_FICHA + 1)

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        lecturas = ultimas_lecturas(cur, maquinaria_id)
        alertas = motor.evaluar_una(cur, maquinaria_id)
        cur.execute(sql, params)
        filas = cur.fetchall()
        sql_m = ("SELECT r.Fecha_Mantencion, r.Horometro_Mecanico, r.Detalle_Trabajo, u.Nombre_Completo "
                 "FROM registro_mantencion r JOIN usuario u ON u.ID_Usuario = r.FK_ID_Usuario "
                 "WHERE r.FK_ID_Maquinaria = %s")
        params_m = [maquinaria_id]
        if desde:
            sql_m += " AND r.Fecha_Mantencion >= %s"
            params_m.append(desde)
        cur.execute(sql_m + " ORDER BY r.Fecha_Mantencion DESC, r.ID_Registro DESC LIMIT %s",
                    params_m + [MAX_MANTENCIONES_FICHA + 1])
        mantenciones = cur.fetchall()

    truncado = len(filas) > MAX_FILAS_FICHA
    pdf = generar_ficha({
        "empresa": current_app.config["EMPRESA_NOMBRE"], "maquina": maquina,
        "foto_path": _ruta_foto(maquina["Fotografia_Principal"]), "lecturas": lecturas, "alertas": alertas,
        "bitacora": filas[:MAX_FILAS_FICHA], "truncado": truncado,
        "mantenciones": mantenciones[:MAX_MANTENCIONES_FICHA],
        "mantenciones_truncadas": len(mantenciones) > MAX_MANTENCIONES_FICHA,
        "periodo_texto": (f"Últimos {dias} días (desde el {desde:%d-%m-%Y})" if dias else "Historial completo"),
        "emitido_por": session.get("nombre", ""), "emitido_en": ahora_})

    patente = re.sub(r"[^A-Za-z0-9_-]", "", maquina["Patente"]) or "maquinaria"
    respuesta = send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=descargar,
                          download_name=f"ficha_{patente}_{ahora_:%Y%m%d}.pdf")
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    respuesta.headers["Cache-Control"] = "no-store"
    return respuesta
