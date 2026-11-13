from io import BytesIO

from PIL import Image

MAQUINA = {"patente": "AB-1234", "modelo": "CAT 320", "anio": "2018", "combustible": "Diésel",
           "chasis": "CHS1", "estado": "Operativa"}
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"


def png_bytes():
    buf = BytesIO()
    Image.new("RGB", (40, 30), "blue").save(buf, "PNG")
    return buf.getvalue()


def preparar(client, login, correo="ana@x.cl", clave="Correcta123"):
    login(correo, clave)
    client.post("/maquinaria/nueva", data=MAQUINA)


def subir(client, contenido=PDF, archivo="Factura 001.pdf", categoria="2", maquina=1, **extra):
    datos = {"categoria": categoria, **extra}
    if contenido is not None:
        datos["archivo"] = (BytesIO(contenido), archivo)
    return client.post(f"/maquinaria/{maquina}/documentos/subir", data=datos, content_type="multipart/form-data")


def docs(bd):
    return bd.execute("SELECT * FROM documento ORDER BY ID_Documento").fetchall()


def test_requiere_login(client):
    assert client.get("/maquinaria/1/documentos/").status_code == 302
    assert client.get("/maquinaria/1/documentos/subir").status_code == 302
    assert client.get("/maquinaria/1/documentos/1/ver").status_code == 302


def test_subir_pdf(client, login, bd, app):
    preparar(client, login)
    r = subir(client)
    assert r.status_code == 302 and r.location.endswith("/maquinaria/1/documentos/")
    d = docs(bd)[0]
    assert d["Nombre_Original"] == "Factura 001.pdf" and d["FK_ID_Categoria"] == 2 and d["FK_ID_Usuario"] == 1
    assert d["Ruta_Archivo"].startswith("documentos/") and d["Ruta_Archivo"].endswith(".pdf")
    assert (app.config["UPLOAD_FOLDER"] / d["Ruta_Archivo"]).read_bytes() == PDF
    assert d["Tamano_Archivo_MB"] == 0.01          # archivo diminuto: se redondea hacia arriba (CHECK > 0)
    html = client.get("/maquinaria/1/documentos/").data.decode()
    assert "Factura 001.pdf" in html and "Factura de compra" in html


def test_subir_imagen_y_nombre_personalizado(client, login, bd):
    preparar(client, login)
    assert subir(client, png_bytes(), "scan.jpeg", categoria="1", nombre="Revisión 2026").status_code == 302
    d = docs(bd)[0]
    assert d["Nombre_Original"] == "Revisión 2026.png" and d["Ruta_Archivo"].endswith(".png")   # extensión según contenido real


def test_rechaza_archivos_invalidos(client, login, bd):
    preparar(client, login)
    assert subir(client, b"esto es texto", "falso.pdf").status_code == 400            # texto disfrazado de PDF
    assert subir(client, b"MZ\x90\x00 programa", "virus.png").status_code == 400      # ejecutable disfrazado
    assert subir(client, b"", "vacio.pdf").status_code == 400
    assert subir(client, None).status_code == 400                                      # sin archivo
    assert subir(client, PDF, categoria="99").status_code == 400                       # categoría inexistente
    assert subir(client, PDF, categoria="").status_code == 400
    assert docs(bd) == []


def test_limite_de_5_mb(client, login, bd):
    preparar(client, login)
    justo = b"%PDF-" + b"0" * (5 * 1024 * 1024 - 5)
    assert subir(client, justo, "justo.pdf").status_code == 302
    excede = b"%PDF-" + b"0" * (5 * 1024 * 1024)
    assert subir(client, excede, "grande.pdf").status_code == 400
    assert len(docs(bd)) == 1 and docs(bd)[0]["Tamano_Archivo_MB"] == 5.00


def test_nombre_malicioso_se_sanea(client, login, bd, app):
    preparar(client, login)
    assert subir(client, PDF, "../../../etc/passwd.pdf").status_code == 302
    d = docs(bd)[0]
    assert d["Nombre_Original"] == "passwd.pdf"
    assert (app.config["UPLOAD_FOLDER"] / d["Ruta_Archivo"]).exists()
    assert not list(app.config["UPLOAD_FOLDER"].parent.glob("etc*"))


def test_ver_y_descargar(client, login):
    preparar(client, login)
    subir(client)
    r = client.get("/maquinaria/1/documentos/1/ver")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data == PDF
    assert r.headers["Content-Disposition"].startswith("inline")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    r = client.get("/maquinaria/1/documentos/1/descargar")
    assert r.status_code == 200 and r.headers["Content-Disposition"].startswith("attachment")
    assert "Factura 001.pdf" in r.headers["Content-Disposition"]


def test_documento_de_otra_maquina_no_es_accesible(client, login):
    preparar(client, login)
    client.post("/maquinaria/nueva", data={**MAQUINA, "patente": "CC-5555", "chasis": "CHS2"})
    subir(client)                                         # documento 1 pertenece a la máquina 1
    for ruta in ("ver", "descargar", "editar"):
        assert client.get(f"/maquinaria/2/documentos/1/{ruta}").status_code == 404
    assert client.post("/maquinaria/2/documentos/1/eliminar").status_code == 404


def test_editar_categoria_y_nombre(client, login, bd):
    preparar(client, login)
    subir(client)
    ruta = docs(bd)[0]["Ruta_Archivo"]
    r = client.post("/maquinaria/1/documentos/1/editar", data={"categoria": "3", "nombre": "Permiso 2026"})
    assert r.status_code == 302
    d = docs(bd)[0]
    assert (d["FK_ID_Categoria"], d["Nombre_Original"], d["Ruta_Archivo"]) == (3, "Permiso 2026.pdf", ruta)
    assert client.post("/maquinaria/1/documentos/1/editar", data={"categoria": "77"}).status_code == 400


def test_editar_reemplazando_el_archivo(client, login, bd, app):
    preparar(client, login)
    subir(client)
    vieja = docs(bd)[0]["Ruta_Archivo"]
    r = client.post("/maquinaria/1/documentos/1/editar", content_type="multipart/form-data",
                    data={"categoria": "2", "nombre": "Factura 001", "archivo": (BytesIO(png_bytes()), "scan.png")})
    assert r.status_code == 302
    d = docs(bd)[0]
    assert d["Ruta_Archivo"] != vieja and d["Ruta_Archivo"].endswith(".png") and d["Nombre_Original"] == "Factura 001.png"
    assert not (app.config["UPLOAD_FOLDER"] / vieja).exists() and (app.config["UPLOAD_FOLDER"] / d["Ruta_Archivo"]).exists()
    # reemplazo inválido: no cambia nada
    r = client.post("/maquinaria/1/documentos/1/editar", content_type="multipart/form-data",
                    data={"categoria": "2", "archivo": (BytesIO(b"basura"), "x.pdf")})
    assert r.status_code == 400 and docs(bd)[0]["Ruta_Archivo"] == d["Ruta_Archivo"]


def test_eliminar_borra_registro_y_archivo(client, login, bd, app):
    preparar(client, login)
    subir(client)
    ruta = docs(bd)[0]["Ruta_Archivo"]
    assert client.get("/maquinaria/1/documentos/1/eliminar").status_code == 405
    r = client.post("/maquinaria/1/documentos/1/eliminar")
    assert r.status_code == 302 and docs(bd) == [] and not (app.config["UPLOAD_FOLDER"] / ruta).exists()


def test_filtro_por_categoria(client, login):
    preparar(client, login)
    subir(client, PDF, "factura.pdf", categoria="2")
    subir(client, PDF, "permiso.pdf", categoria="3")
    client.get("/maquinaria/1/documentos/")          # consume los mensajes flash pendientes
    html = client.get("/maquinaria/1/documentos/?categoria=3").data.decode()
    assert "permiso.pdf" in html and "factura.pdf" not in html


def test_rol_de_solo_lectura(client, login, bd):
    preparar(client, login)
    subir(client)
    client.post("/logout")
    login("ger@x.cl", "Gerente123")
    assert client.get("/maquinaria/1/documentos/").status_code == 200
    assert client.get("/maquinaria/1/documentos/1/ver").status_code == 200
    assert client.get("/maquinaria/1/documentos/1/descargar").status_code == 200
    html = client.get("/maquinaria/1/documentos/").data.decode()
    assert "Subir documento" not in html and "Eliminar" not in html
    assert client.get("/maquinaria/1/documentos/subir").status_code == 302
    assert subir(client, PDF, "otro.pdf").status_code == 302 and len(docs(bd)) == 1
    assert client.post("/maquinaria/1/documentos/1/eliminar").status_code == 302 and len(docs(bd)) == 1
    assert client.post("/maquinaria/1/documentos/1/editar", data={"categoria": "1"}).status_code == 302
    assert docs(bd)[0]["FK_ID_Categoria"] == 2


def test_perfil_muestra_cantidad_y_maquina_con_documentos_no_se_elimina(client, login, bd):
    preparar(client, login)
    subir(client)
    assert "1 archivo(s) adjunto(s)" in client.get("/maquinaria/1").data.decode()
    client.post("/maquinaria/1/eliminar")
    assert bd.execute("SELECT COUNT(*) AS n FROM maquinaria").fetchone()["n"] == 1


def test_maquina_inexistente(client, login):
    login("ana@x.cl", "Correcta123")
    assert client.get("/maquinaria/99/documentos/").status_code == 404
    assert client.get("/maquinaria/99/documentos/subir").status_code == 404
