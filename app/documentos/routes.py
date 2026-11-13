"""Gestor documental (RF3): adjuntar, visualizar, descargar, editar y eliminar archivos
(PDF e imágenes) de cada maquinaria, clasificados por categoría."""
import re
from contextlib import closing

from flask import (Blueprint, abort, current_app, flash, redirect, render_template,
                   request, send_from_directory, session, url_for)

from app import db
from app.archivos import (TIPOS, borrar_archivo, detectar_tipo, guardar_archivo,
                          leer_subida, tamano_mb)
from app.maquinaria.routes import _obtener
from app.security import escritura_required, login_required
from app.utils import ahora

bp = Blueprint("documentos", __name__, url_prefix="/maquinaria/<int:maquinaria_id>/documentos")

EXTENSIONES_CONOCIDAS = {"pdf", "jpg", "jpeg", "jpe", "png", "webp"}
SELECT_DOC = ("SELECT d.ID_Documento, d.FK_ID_Maquinaria, d.FK_ID_Categoria, d.Ruta_Archivo, "
              "d.Nombre_Original, d.Tamano_Archivo_MB, d.Fecha_Subida, "
              "c.Nombre_Categoria, u.Nombre_Completo "
              "FROM documento d JOIN categoria_documento c ON c.ID_Categoria = d.FK_ID_Categoria "
              "JOIN usuario u ON u.ID_Usuario = d.FK_ID_Usuario ")


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _stem(texto):
    """Nombre limpio y sin extensión: sin rutas, sin caracteres de control, máx. 200 caracteres."""
    texto = (texto or "").replace("\\", "/").split("/")[-1]
    texto = re.sub(r"[\x00-\x1f\x7f]", "", texto)
    if "." in texto and texto.rsplit(".", 1)[1].lower() in EXTENSIONES_CONOCIDAS:
        texto = texto.rsplit(".", 1)[0]
    texto = re.sub(r"\s+", " ", texto).strip(" .")
    return texto[:200] or "documento"


def _extension(ruta):
    return ruta.rsplit(".", 1)[1]


def _categorias(cur):
    cur.execute("SELECT ID_Categoria, Nombre_Categoria FROM categoria_documento ORDER BY ID_Categoria")
    return cur.fetchall()


def _leer_categoria(form, categorias, errores):
    valor = form.get("categoria", "")
    if valor not in {str(c["ID_Categoria"]) for c in categorias}:
        errores.append("Selecciona una categoría válida.")
        return None
    return int(valor)


def _leer_archivo(campo, errores):
    """Devuelve (contenido, extension) del archivo subido o (None, None)."""
    archivo = request.files.get(campo)
    if not archivo or not archivo.filename:
        return None, None
    try:
        contenido = leer_subida(archivo)
        return contenido, detectar_tipo(contenido)
    except ValueError as error:
        errores.append(str(error))
        return None, None


def _obtener_documento(cur, maquinaria_id, documento_id):
    cur.execute(SELECT_DOC + "WHERE d.ID_Documento = %s AND d.FK_ID_Maquinaria = %s",
                (documento_id, maquinaria_id))
    doc = cur.fetchone()
    if doc is None:
        abort(404)
    return doc


def _formulario(maquina, doc, categorias, datos, errores, codigo=200):
    for error in errores:
        flash(error, "danger")
    return render_template("documentos/form.html", maquina=maquina, doc=doc,
                           categorias=categorias, datos=datos), codigo


def _enviar(maquinaria_id, documento_id, descargar):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        doc = _obtener_documento(cur, maquinaria_id, documento_id)
    ruta = doc["Ruta_Archivo"]
    respuesta = send_from_directory(
        current_app.config["UPLOAD_FOLDER"], ruta, mimetype=TIPOS.get(_extension(ruta)),
        as_attachment=descargar, download_name=doc["Nombre_Original"])
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    return respuesta


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def lista(maquinaria_id):
    categoria = request.args.get("categoria", type=int)
    sql, params = SELECT_DOC + "WHERE d.FK_ID_Maquinaria = %s", [maquinaria_id]
    if categoria:
        sql += " AND d.FK_ID_Categoria = %s"
        params.append(categoria)
    sql += " ORDER BY d.Fecha_Subida DESC, d.ID_Documento DESC"

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        categorias = _categorias(cur)
        cur.execute(sql, params)
        documentos = cur.fetchall()
    return render_template("documentos/lista.html", maquina=maquina, documentos=documentos,
                           categorias=categorias, categoria=categoria)


@bp.route("/subir", methods=["GET", "POST"])
@login_required
@escritura_required
def subir(maquinaria_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        categorias = _categorias(cur)
        if request.method == "GET":
            return _formulario(maquina, None, categorias, {}, [])

        datos = {"categoria": request.form.get("categoria", ""), "nombre": request.form.get("nombre", "")}
        errores = []
        categoria_id = _leer_categoria(request.form, categorias, errores)
        contenido, extension = _leer_archivo("archivo", errores)
        if contenido is None and not errores:
            errores.append("Selecciona un archivo (PDF o imagen).")
        if errores:
            return _formulario(maquina, None, categorias, datos, errores, 400)

        nombre = f"{_stem(datos['nombre'] or request.files['archivo'].filename)}.{extension}"
        ruta = guardar_archivo("documentos", contenido, extension)
        try:
            cur.execute(
                "INSERT INTO documento (FK_ID_Maquinaria, FK_ID_Usuario, FK_ID_Categoria, Ruta_Archivo, "
                "Nombre_Original, Tamano_Archivo_MB, Fecha_Subida) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (maquinaria_id, session["usuario_id"], categoria_id, ruta, nombre,
                 tamano_mb(contenido), ahora()))
            conn.commit()
        except Exception:
            conn.rollback()
            borrar_archivo(ruta)
            raise

    flash(f"Documento «{nombre}» subido correctamente.", "success")
    return redirect(url_for("documentos.lista", maquinaria_id=maquinaria_id))


@bp.route("/<int:documento_id>/ver")
@login_required
def ver(maquinaria_id, documento_id):
    return _enviar(maquinaria_id, documento_id, descargar=False)


@bp.route("/<int:documento_id>/descargar")
@login_required
def descargar(maquinaria_id, documento_id):
    return _enviar(maquinaria_id, documento_id, descargar=True)


@bp.route("/<int:documento_id>/editar", methods=["GET", "POST"])
@login_required
@escritura_required
def editar(maquinaria_id, documento_id):
    """Permite cambiar la categoría y el nombre, y opcionalmente reemplazar el archivo."""
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        doc = _obtener_documento(cur, maquinaria_id, documento_id)
        categorias = _categorias(cur)
        if request.method == "GET":
            return _formulario(maquina, doc, categorias,
                               {"categoria": str(doc["FK_ID_Categoria"]), "nombre": _stem(doc["Nombre_Original"])}, [])

        datos = {"categoria": request.form.get("categoria", ""), "nombre": request.form.get("nombre", "")}
        errores = []
        categoria_id = _leer_categoria(request.form, categorias, errores)
        contenido, extension = _leer_archivo("archivo", errores)
        if errores:
            return _formulario(maquina, doc, categorias, datos, errores, 400)

        ruta_anterior = doc["Ruta_Archivo"]
        ruta, tamano = ruta_anterior, doc["Tamano_Archivo_MB"]
        if contenido is not None:
            ruta = guardar_archivo("documentos", contenido, extension)
            tamano = tamano_mb(contenido)
        nombre = f"{_stem(datos['nombre'] or doc['Nombre_Original'])}.{_extension(ruta)}"
        try:
            cur.execute(
                "UPDATE documento SET FK_ID_Categoria = %s, Nombre_Original = %s, Ruta_Archivo = %s, "
                "Tamano_Archivo_MB = %s WHERE ID_Documento = %s AND FK_ID_Maquinaria = %s",
                (categoria_id, nombre, ruta, tamano, documento_id, maquinaria_id))
            conn.commit()
        except Exception:
            conn.rollback()
            if ruta != ruta_anterior:
                borrar_archivo(ruta)
            raise

    if ruta != ruta_anterior:
        borrar_archivo(ruta_anterior)
    flash("Documento actualizado correctamente.", "success")
    return redirect(url_for("documentos.lista", maquinaria_id=maquinaria_id))


@bp.route("/<int:documento_id>/eliminar", methods=["POST"])
@login_required
@escritura_required
def eliminar(maquinaria_id, documento_id):
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        doc = _obtener_documento(cur, maquinaria_id, documento_id)
        cur.execute("DELETE FROM documento WHERE ID_Documento = %s AND FK_ID_Maquinaria = %s",
                    (documento_id, maquinaria_id))
        conn.commit()
    borrar_archivo(doc["Ruta_Archivo"])
    flash(f"Documento «{doc['Nombre_Original']}» eliminado.", "success")
    return redirect(url_for("documentos.lista", maquinaria_id=maquinaria_id))
