from contextlib import closing

from flask import Blueprint, redirect, render_template, session, url_for

from app import db
from app.alertas import motor
from app.maquinaria.routes import ESTADOS
from app.security import login_required

bp = Blueprint("main", __name__)


@bp.route("/")
def inicio():
    return redirect(url_for("main.dashboard"))


@bp.route("/dashboard")
@login_required
def dashboard():
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        cur.execute("SELECT Estado_Operativo_Actual AS estado, COUNT(*) AS n "
                    "FROM maquinaria GROUP BY Estado_Operativo_Actual")
        filas = cur.fetchall()
    resumen = {estado: 0 for estado in ESTADOS}
    for fila in filas:
        resumen[fila["estado"]] = fila["n"]
    resumen["total"] = sum(resumen[e] for e in ESTADOS)
    flota = motor.flota_alertas()
    return render_template("main/dashboard.html", nombre=session.get("nombre"), rol=session.get("rol"),
                           resumen=resumen, alertas=motor.resumen(flota),
                           principales=motor.alertas_ordenadas(flota)[:8])
