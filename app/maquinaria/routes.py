"""Perfiles de flota (RF1): listar, crear, ver, editar y eliminar maquinaria."""
import re
from contextlib import closing
from io import BytesIO

import pymysql
from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, send_from_directory, session, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError

from app import db
from app.alertas import motor as motor_alertas
from app.auditoria.registro import ACCION_MODIFICACION, registrar
from app.archivos import MAX_BYTES, borrar_archivo, guardar_archivo
from app.bitacora.queries import ultimas_lecturas
from app.combustible.queries import ultima_carga
from app.documentos.queries import contar_documentos
from app.mantenciones.queries import contar_mantenciones, ultima_mantencion
from app.security import escritura_required, login_required
from app.utils import ahora

bp = Blueprint("maquinaria", __name__, url_prefix="/maquinaria")

ESTADOS = ("Operativa", "En taller/Mantención", "Inactiva")
TIPOS_COMBUSTIBLE = ("Diésel", "Gasolina", "Gas", "Eléctrico", "Híbrido", "Otro")
ANIO_MIN = 1900
PATRON_PATENTE = re.compile(r"^[A-Z0-9\-]{3,20}$")
FORMATOS_FOTO = {"JPEG", "PNG", "WEBP"}
COLUMNAS = ("ID_Maquinaria, Patente, Modelo, Anio, Tipo_Combustible, Numero_Chasis, "
            "Fotografia_Principal, Estado_Operativo_Actual")

# Tablas que guardan historial de una máquina (impiden eliminarla)
TABLAS_HISTORIAL = {
    "bitacora_operativa": "registros de bitácora",
    "documento": "documentos",
    "carga_combustible": "cargas de combustible",
    "alerta_personalizada": "alertas personalizadas",
    "registro_mantencion": "mantenciones",
    "auditoria": "registros de auditoría",
}


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _procesar_foto(archivo):
    """Valida que sea una imagen real, la reduce (máx. 1600 px) y la devuelve como JPEG."""
    archivo.stream.seek(0, 2)
    tamano = archivo.stream.tell()
    archivo.stream.seek(0)
    if tamano > MAX_BYTES:
        raise ValueError("La fotografía supera el límite de 5 MB.")
    try:
        imagen = Image.open(archivo.stream)
        if imagen.format not in FORMATOS_FOTO:
            raise ValueError
        imagen.verify()
        archivo.stream.seek(0)
        imagen = ImageOps.exif_transpose(Image.open(archivo.stream)).convert("RGB")
        imagen.thumbnail((1600, 1600))
        salida = BytesIO()
        imagen.save(salida, "JPEG", quality=85, optimize=True)
        return salida.getvalue()
    except (ValueError, UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError("La fotografía debe ser una imagen JPG, PNG o WEBP válida.")


def _guardar_foto(contenido):
    return guardar_archivo("fotos", contenido, "jpg")


def _borrar_foto(ruta):
    borrar_archivo(ruta)


def _obtener(cur, maquinaria_id):
    cur.execute(f"SELECT {COLUMNAS} FROM maquinaria WHERE ID_Maquinaria = %s", (maquinaria_id,))
    maquina = cur.fetchone()
    if maquina is None:
        abort(404)
    return maquina


def _validar_nueva(form):
    errores = []
    datos = {
        "patente": form.get("patente", "").strip().upper(),
        "modelo": form.get("modelo", "").strip(),
        "anio": form.get("anio", "").strip(),
        "combustible": form.get("combustible", ""),
        "chasis": form.get("chasis", "").strip().upper(),
        "estado": form.get("estado", ""),
    }
    if not PATRON_PATENTE.match(datos["patente"]):
        errores.append("La patente debe tener entre 3 y 20 caracteres (letras, números o guion).")
    if not datos["modelo"] or len(datos["modelo"]) > 100:
        errores.append("El modelo es obligatorio (máximo 100 caracteres).")
    anio_max = ahora().year + 1
    if not (datos["anio"].isdigit() and ANIO_MIN <= int(datos["anio"]) <= anio_max):
        errores.append(f"El año debe estar entre {ANIO_MIN} y {anio_max}.")
    if datos["combustible"] not in TIPOS_COMBUSTIBLE:
        errores.append("Selecciona un tipo de combustible válido.")
    if not datos["chasis"] or len(datos["chasis"]) > 100:
        errores.append("El número de chasis es obligatorio (máximo 100 caracteres).")
    if datos["estado"] not in ESTADOS:
        errores.append("Selecciona un estado operativo válido.")
    return datos, errores


def _snapshot_base(patente, modelo, anio, combustible, chasis):
    """Datos base tal como se guardan en la auditoría (texto legible)."""
    return {"Patente": str(patente), "Modelo": str(modelo), "Año": str(int(anio)),
            "Tipo de combustible": str(combustible), "N° de chasis": str(chasis)}


def _duplicados(cur, patente, chasis, excluir_id=None):
    errores = []
    cur.execute("SELECT 1 AS x FROM maquinaria WHERE Patente = %s AND ID_Maquinaria <> %s", (patente, excluir_id or 0))
    if cur.fetchone():
        errores.append(f"Ya existe una maquinaria con la patente {patente}.")
    cur.execute("SELECT 1 AS x FROM maquinaria WHERE Numero_Chasis = %s AND ID_Maquinaria <> %s", (chasis, excluir_id or 0))
    if cur.fetchone():
        errores.append(f"Ya existe una maquinaria con el N° de chasis {chasis}.")
    return errores


def _leer_foto(errores):
    """Devuelve los bytes de la foto subida (o None); agrega el error si no es válida."""
    archivo = request.files.get("foto")
    if archivo and archivo.filename:
        try:
            return _procesar_foto(archivo)
        except ValueError as error:
            errores.append(str(error))
    return None


def _formulario(maquina, datos, errores, codigo=200):
    for error in errores:
        flash(error, "danger")
    return render_template("maquinaria/form.html", maquina=maquina, datos=datos,
                           estados=ESTADOS, tipos=TIPOS_COMBUSTIBLE), codigo


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def lista():
    q = request.args.get("q", "").strip()
    estado = request.args.get("estado", "")
    sql = f"SELECT {COLUMNAS} FROM maquinaria WHERE 1 = 1"
    params = []
    if q:
        sql += " AND (Patente LIKE %s OR Modelo LIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    if estado in ESTADOS:
        sql += " AND Estado_Operativo_Actual = %s"
        params.append(estado)
    sql += " ORDER BY Patente"

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        maquinas = cur.fetchall()
    semaforos = motor_alertas.semaforos(motor_alertas.flota_alertas())
    return render_template("maquinaria/lista.html", maquinas=maquinas, q=q,
                           estado=estado, estados=ESTADOS, semaforos=semaforos)


@bp.route("/nueva", methods=["GET", "POST"])
@login_required
@escritura_required
def nueva():
    if request.method == "GET":
        return _formulario(None, {"estado": "Operativa"}, [])

    datos, errores = _validar_nueva(request.form)
    foto = _leer_foto(errores)

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        if not errores:
            errores = _duplicados(cur, datos["patente"], datos["chasis"])
        if errores:
            return _formulario(None, datos, errores, 400)

        ruta = _guardar_foto(foto) if foto else None
        try:
            cur.execute(
                "INSERT INTO maquinaria (Patente, Modelo, Anio, Tipo_Combustible, Numero_Chasis, "
                "Fotografia_Principal, Estado_Operativo_Actual) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (datos["patente"], datos["modelo"], int(datos["anio"]), datos["combustible"],
                 datos["chasis"], ruta, datos["estado"]),
            )
            nuevo_id = cur.lastrowid
            # Cada máquina tiene exactamente un panel de alertas estáticas (relación 1 a 1)
            cur.execute("INSERT INTO alerta_estatica (FK_ID_Maquinaria) VALUES (%s)", (nuevo_id,))
            conn.commit()
        except pymysql.err.IntegrityError:
            conn.rollback()
            _borrar_foto(ruta)
            return _formulario(None, datos, ["La patente o el N° de chasis ya está registrado."], 400)
        except Exception:
            conn.rollback()
            _borrar_foto(ruta)
            raise

    flash(f"Maquinaria {datos['patente']} registrada correctamente.", "success")
    return redirect(url_for("maquinaria.perfil", maquinaria_id=nuevo_id))


@bp.route("/<int:maquinaria_id>")
@login_required
def perfil(maquinaria_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        lecturas = ultimas_lecturas(cur, maquinaria_id)
        n_documentos = contar_documentos(cur, maquinaria_id)
        carga = ultima_carga(cur, maquinaria_id)
        mantencion = ultima_mantencion(cur, maquinaria_id)
        n_mantenciones = contar_mantenciones(cur, maquinaria_id)
        alertas = motor_alertas.evaluar_una(cur, maquinaria_id)
        personalizadas = motor_alertas.personalizadas_de_maquina(cur, maquinaria_id)
    return render_template("maquinaria/perfil.html", maquina=maquina, lecturas=lecturas,
                           n_documentos=n_documentos, carga=carga, alertas=alertas,
                           personalizadas=personalizadas, mantencion=mantencion, n_mantenciones=n_mantenciones)


@bp.route("/<int:maquinaria_id>/editar", methods=["GET", "POST"])
@login_required
@escritura_required
def editar(maquinaria_id):
    """Cambia el estado operativo y la fotografía. Los datos base (patente, modelo, año, combustible,
    chasis) son inmutables, salvo para los roles de ROLES_CORRECCION_BASE (Super Administrador),
    cuyas correcciones quedan registradas en la auditoría."""
    corrige = session.get("rol") in current_app.config["ROLES_CORRECCION_BASE"]
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        if request.method == "GET":
            datos = {"estado": maquina["Estado_Operativo_Actual"]}
            if corrige:
                datos.update(patente=maquina["Patente"], modelo=maquina["Modelo"], anio=str(maquina["Anio"]),
                             combustible=maquina["Tipo_Combustible"], chasis=maquina["Numero_Chasis"])
            return _formulario(maquina, datos, [])

        if corrige:
            datos, errores = _validar_nueva(request.form)
            estado = datos["estado"]
            if not errores:
                errores = _duplicados(cur, datos["patente"], datos["chasis"], maquinaria_id)
        else:
            errores = []
            estado = request.form.get("estado", "")
            datos = {"estado": estado}
            if estado not in ESTADOS:
                errores.append("Selecciona un estado operativo válido.")
        foto = _leer_foto(errores)
        if errores:
            return _formulario(maquina, datos, errores, 400)

        ruta_anterior = maquina["Fotografia_Principal"]
        ruta = ruta_anterior
        if foto:
            ruta = _guardar_foto(foto)
        elif request.form.get("quitar_foto"):
            ruta = None
        try:
            if corrige:
                antes = _snapshot_base(maquina["Patente"], maquina["Modelo"], maquina["Anio"],
                                       maquina["Tipo_Combustible"], maquina["Numero_Chasis"])
                despues = _snapshot_base(datos["patente"], datos["modelo"], datos["anio"],
                                         datos["combustible"], datos["chasis"])
                cur.execute(
                    "UPDATE maquinaria SET Patente = %s, Modelo = %s, Anio = %s, Tipo_Combustible = %s, "
                    "Numero_Chasis = %s, Estado_Operativo_Actual = %s, Fotografia_Principal = %s "
                    "WHERE ID_Maquinaria = %s",
                    (datos["patente"], datos["modelo"], int(datos["anio"]), datos["combustible"],
                     datos["chasis"], estado, ruta, maquinaria_id))
                if antes != despues:          # cambiar solo estado o foto no es una corrección
                    registrar(cur, "maquinaria", maquinaria_id, ACCION_MODIFICACION, antes, despues, maquinaria_id)
            else:
                cur.execute(
                    "UPDATE maquinaria SET Estado_Operativo_Actual = %s, Fotografia_Principal = %s "
                    "WHERE ID_Maquinaria = %s", (estado, ruta, maquinaria_id))
            conn.commit()
        except pymysql.err.IntegrityError:
            conn.rollback()
            if ruta != ruta_anterior:
                _borrar_foto(ruta)
            return _formulario(maquina, datos, ["La patente o el N° de chasis ya está registrado."], 400)
        except Exception:
            conn.rollback()
            if ruta != ruta_anterior:
                _borrar_foto(ruta)
            raise

    if ruta != ruta_anterior:
        _borrar_foto(ruta_anterior)
    flash("Perfil actualizado correctamente.", "success")
    return redirect(url_for("maquinaria.perfil", maquinaria_id=maquinaria_id))


@bp.route("/<int:maquinaria_id>/eliminar", methods=["POST"])
@login_required
@escritura_required
def eliminar(maquinaria_id):
    """Elimina el perfil solo si no tiene historial; si lo tiene, hay que marcarla como Inactiva."""
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        subconsultas = ", ".join(
            f"(SELECT COUNT(*) FROM {tabla} WHERE FK_ID_Maquinaria = %s) AS {tabla}"
            for tabla in TABLAS_HISTORIAL)
        cur.execute(f"SELECT {subconsultas}", [maquinaria_id] * len(TABLAS_HISTORIAL))
        conteos = cur.fetchone()
        con_historial = [f"{conteos[t]} {texto}" for t, texto in TABLAS_HISTORIAL.items() if conteos[t]]

        if con_historial:
            flash("No se puede eliminar: la maquinaria tiene historial (" + ", ".join(con_historial) +
                  "). Para sacarla de operación, cambia su estado a «Inactiva».", "danger")
            return redirect(url_for("maquinaria.perfil", maquinaria_id=maquinaria_id))

        cur.execute("DELETE FROM alerta_estatica WHERE FK_ID_Maquinaria = %s", (maquinaria_id,))
        cur.execute("DELETE FROM maquinaria WHERE ID_Maquinaria = %s", (maquinaria_id,))
        conn.commit()

    _borrar_foto(maquina["Fotografia_Principal"])
    flash(f"Maquinaria {maquina['Patente']} eliminada.", "success")
    return redirect(url_for("maquinaria.lista"))


@bp.route("/<int:maquinaria_id>/foto")
@login_required
def foto(maquinaria_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        cur.execute("SELECT Fotografia_Principal FROM maquinaria WHERE ID_Maquinaria = %s", (maquinaria_id,))
        fila = cur.fetchone()
    if not fila or not fila["Fotografia_Principal"]:
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], fila["Fotografia_Principal"])
