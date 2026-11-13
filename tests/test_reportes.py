import re
from datetime import timedelta
from io import BytesIO

from PIL import Image
from pypdf import PdfReader

from app.reportes import routes as rutas_reportes
from app.utils import ahora

MAQ = {"patente": "AB-1234", "modelo": "CAT 320 Excavadora", "anio": "2018", "combustible": "Diésel",
       "chasis": "CHS1ÑU", "estado": "Operativa"}


def hoy_app(app):
    with app.app_context():
        return ahora().date()


def preparar(client, login, foto=False):
    login("ana@x.cl", "Correcta123")
    datos = dict(MAQ)
    if foto:
        buf = BytesIO()
        Image.new("RGB", (400, 300), "orange").save(buf, "PNG")
        buf.seek(0)
        datos["foto"] = (buf, "foto.png")
    client.post("/maquinaria/nueva", data=datos, content_type="multipart/form-data")


def bitacora(bd, app, dias_atras, estado="Operativo", horometro=None):
    fecha = hoy_app(app) - timedelta(days=dias_atras)
    bd.execute("INSERT INTO bitacora_operativa(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Registro, Horometro, "
               "Estado_Diario) VALUES (1, 1, ?, ?, ?)", (fecha.isoformat(), horometro, estado))
    bd.commit()


def leer(respuesta):
    lector = PdfReader(BytesIO(respuesta.data))
    return lector, "\n".join(p.extract_text() for p in lector.pages)


def test_requiere_login_y_maquina_existente(client, login):
    assert client.get("/maquinaria/1/ficha.pdf").status_code == 302
    login("ana@x.cl", "Correcta123")
    assert client.get("/maquinaria/99/ficha.pdf").status_code == 404


def test_devuelve_un_pdf_para_ver_o_descargar(app, client, login):
    preparar(client, login)
    r = client.get("/maquinaria/1/ficha.pdf")
    assert r.status_code == 200 and r.mimetype == "application/pdf" and r.data.startswith(b"%PDF")
    assert r.headers["Content-Disposition"].startswith("inline")            # «Ver / imprimir» en el navegador
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cache-Control"] == "no-store"
    r = client.get("/maquinaria/1/ficha.pdf?descargar=1")
    cd = r.headers["Content-Disposition"]
    assert cd.startswith("attachment") and re.search(r"ficha_AB-1234_\d{8}\.pdf", cd)


def test_contenido_de_la_ficha(app, client, login, bd):
    preparar(client, login)
    h = hoy_app(app)
    client.post("/maquinaria/1/alertas/editar", data={"revision": (h + timedelta(days=12)).isoformat(),
                                                      "permiso": (h - timedelta(days=3)).isoformat(), "mantencion": "1.500"})
    client.post("/maquinaria/1/alertas/personalizadas/nueva",
                data={"titulo": "Cambio de neumáticos", "fecha": (h + timedelta(days=4)).isoformat()})
    bitacora(bd, app, 2, "Operativo", 1470.5)
    bitacora(bd, app, 1, "Día en taller")
    lector, texto = leer(client.get("/maquinaria/1/ficha.pdf"))
    for esperado in ("FICHA TÉCNICA DE MAQUINARIA", "Áridos y Maquinaria Fuentealba", "AB-1234", "CAT 320 Excavadora",
                     "Diésel", "CHS1ÑU", "2018", "Operativa", "1.470,50 h", "Ana Test",
                     "Con vencimientos", "Vence en 12 día(s)", "Vencida hace 3 día(s)", "Faltan 29,50 h",
                     "Cambio de neumáticos", "(personalizada)", "Día en taller", "Página 1 de 1"):
        assert esperado in texto, esperado
    assert len(lector.pages) == 1


def test_el_texto_de_usuario_no_se_interpreta_como_marcado(app, client, login, bd):
    preparar(client, login)
    bitacora(bd, app, 1, "<b>negrita</b> & <img src='file:///etc/passwd'> <font color=red>")
    client.post("/maquinaria/1/alertas/personalizadas/nueva",
                data={"titulo": "Luz & freno <urgente>", "fecha": (hoy_app(app) + timedelta(days=2)).isoformat()})
    r = client.get("/maquinaria/1/ficha.pdf")
    assert r.status_code == 200
    _, texto = leer(r)
    assert "<b>negrita</b>" in texto and "<img src='file:///etc/passwd'>" in texto and "Luz & freno <urgente>" in texto


def test_filtro_de_periodo(app, client, login, bd):
    preparar(client, login)
    bitacora(bd, app, 5, "reciente")
    bitacora(bd, app, 60, "intermedio")
    bitacora(bd, app, 400, "antiguo")
    texto = lambda q: leer(client.get(f"/maquinaria/1/ficha.pdf{q}"))[1]
    t30 = texto("?periodo=30")
    assert "reciente" in t30 and "intermedio" not in t30 and "Últimos 30 días" in t30
    t90 = texto("")                                           # por defecto: 90 días
    assert "intermedio" in t90 and "antiguo" not in t90 and "Últimos 90 días" in t90
    assert "antiguo" not in texto("?periodo=365") and "intermedio" in texto("?periodo=365")
    todo = texto("?periodo=todo")
    assert "antiguo" in todo and "Historial completo" in todo
    assert "Últimos 90 días" in texto("?periodo=abc")           # valor inválido: vuelve al valor por defecto


def test_sin_registros(client, login):
    preparar(client, login)
    assert "Sin registros en el período seleccionado." in leer(client.get("/maquinaria/1/ficha.pdf"))[1]


def test_incluye_la_fotografia_si_existe(client, login):
    preparar(client, login, foto=True)
    lector, texto = leer(client.get("/maquinaria/1/ficha.pdf"))
    assert len(lector.pages[0].images) == 1 and "Sin fotografía" not in texto


def test_sin_fotografia_muestra_marcador(client, login):
    preparar(client, login)
    lector, texto = leer(client.get("/maquinaria/1/ficha.pdf"))
    assert len(lector.pages[0].images) == 0 and "Sin fotografía" in texto


def test_foto_faltante_en_disco_no_rompe_la_ficha(app, client, login, bd):
    preparar(client, login, foto=True)
    ruta = bd.execute("SELECT Fotografia_Principal AS f FROM maquinaria").fetchone()["f"]
    (app.config["UPLOAD_FOLDER"] / ruta).unlink()
    r = client.get("/maquinaria/1/ficha.pdf")
    assert r.status_code == 200 and "Sin fotografía" in leer(r)[1]


def test_historial_largo_ocupa_varias_paginas_con_cabecera_repetida(app, client, login, bd):
    preparar(client, login)
    for i in range(150):
        bitacora(bd, app, i + 1, f"registro-{i:03d}")
    lector, texto = leer(client.get("/maquinaria/1/ficha.pdf"))
    n = len(lector.pages)
    assert n >= 3 and f"Página 1 de {n}" in texto and f"Página {n} de {n}" in texto
    assert "registro-000" in texto and "registro-089" in texto and "registro-090" not in texto   # 90 días
    con_filas = [p.extract_text() for p in lector.pages if "registro-" in p.extract_text()]
    assert len(con_filas) >= 2 and all("Estado del día" in t for t in con_filas)    # cabecera repetida en cada página


def test_tope_de_filas(app, client, login, bd, monkeypatch):
    monkeypatch.setattr(rutas_reportes, "MAX_FILAS_FICHA", 5)
    preparar(client, login)
    for i in range(8):
        bitacora(bd, app, i + 1, f"registro-{i}")
    _, texto = leer(client.get("/maquinaria/1/ficha.pdf?periodo=todo"))
    assert "Se muestran los 5 registros más recientes" in texto
    assert "registro-4" in texto and "registro-5" not in texto


def test_maquina_inactiva_lo_aclara(client, login):
    preparar(client, login)
    client.post("/maquinaria/1/editar", data={"estado": "Inactiva"})
    _, texto = leer(client.get("/maquinaria/1/ficha.pdf"))
    assert "No aplica (maquinaria inactiva)" in texto and "no se incluye en los avisos generales" in texto


def test_cualquier_rol_puede_exportar(client, login):
    preparar(client, login)
    client.post("/logout")
    login("ger@x.cl", "Gerente123")
    r = client.get("/maquinaria/1/ficha.pdf")
    assert r.status_code == 200 and "Gerente Test" in leer(r)[1]        # queda registrado quién la emitió


def test_el_perfil_ofrece_ver_e_imprimir_y_descargar(client, login):
    preparar(client, login)
    html = client.get("/maquinaria/1").data.decode()
    assert "/maquinaria/1/ficha.pdf" in html and "Ver / imprimir" in html and "Descargar PDF" in html
    assert 'name="periodo"' in html


def mantencion(bd, app, dias_atras, detalle, lectura=1000):
    fecha = hoy_app(app) - timedelta(days=dias_atras)
    bd.execute("INSERT INTO registro_mantencion(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Mantencion, "
               "Horometro_Mecanico, Detalle_Trabajo) VALUES (1, 1, ?, ?, ?)", (fecha.isoformat(), lectura, detalle))
    bd.commit()


def test_la_ficha_incluye_el_historial_de_mantenciones(app, client, login, bd):
    preparar(client, login)
    mantencion(bd, app, 20, "Cambio de aceite de motor y filtros\nRevisión del sistema hidráulico", 1480.5)
    mantencion(bd, app, 400, "Reparación antigua fuera de período", 900)
    _, texto = leer(client.get("/maquinaria/1/ficha.pdf"))
    assert "4. Historial de mantenciones" in texto and "Cambio de aceite de motor" in texto
    assert "Revisión del sistema hidráulico" in texto and "1.480,50" in texto and "Reparación antigua" not in texto
    assert "Reparación antigua" in leer(client.get("/maquinaria/1/ficha.pdf?periodo=todo"))[1]
    assert "Cambio de aceite" in leer(client.get("/maquinaria/1/ficha.pdf?periodo=30"))[1]        # 20 días: dentro de 30


def test_ficha_sin_mantenciones(client, login):
    preparar(client, login)
    assert "Sin mantenciones registradas en el período seleccionado." in leer(client.get("/maquinaria/1/ficha.pdf"))[1]


def test_textos_enormes_de_mantencion_no_rompen_la_ficha(app, client, login, bd):
    preparar(client, login)
    mantencion(bd, app, 3, "palabra " * 7000)                            # ~56.000 caracteres seguidos
    mantencion(bd, app, 4, "\n".join(f"linea {i}" for i in range(4000)))  # 4.000 saltos de línea
    mantencion(bd, app, 5, "<img src='file:///etc/passwd'> & <b>x</b>")
    r = client.get("/maquinaria/1/ficha.pdf")
    assert r.status_code == 200
    _, texto = leer(r)
    assert "Texto completo disponible en el sistema" in texto and "<img src='file:///etc/passwd'>" in texto


def test_tope_de_mantenciones_en_la_ficha(app, client, login, bd, monkeypatch):
    monkeypatch.setattr(rutas_reportes, "MAX_MANTENCIONES_FICHA", 2)
    preparar(client, login)
    for i in range(4):
        mantencion(bd, app, i + 1, f"trabajo-{i}")
    _, texto = leer(client.get("/maquinaria/1/ficha.pdf"))
    assert "Se muestran las 2 mantenciones más recientes" in texto and "trabajo-1" in texto and "trabajo-2" not in texto
