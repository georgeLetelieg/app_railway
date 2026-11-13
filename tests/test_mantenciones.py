from datetime import timedelta

from app.utils import ahora

MAQ = {"patente": "AB-1234", "modelo": "CAT 320", "anio": "2018", "combustible": "Diésel",
       "chasis": "CHS1", "estado": "Operativa"}


def hoy_app(app):
    with app.app_context():
        return ahora().date()


def preparar(client, login, n=1):
    login("ana@x.cl", "Correcta123")
    for i in range(n):
        client.post("/maquinaria/nueva", data={**MAQ, "patente": f"AB-123{i}", "chasis": f"CHS{i}"})


def registrar(client, app, dias_atras=0, lectura="1.000", detalle="Cambio de aceite y filtros", maquina=1, **extra):
    fecha = hoy_app(app) - timedelta(days=dias_atras)
    return client.post(f"/maquinaria/{maquina}/mantenciones/nueva",
                       data={"fecha": fecha.isoformat(), "lectura": lectura, "detalle": detalle, **extra})


def filas(bd):
    return bd.execute("SELECT * FROM registro_mantencion ORDER BY ID_Registro").fetchall()


def meta(bd):
    return bd.execute("SELECT Proxima_Mantencion_Preventiva AS m FROM alerta_estatica WHERE FK_ID_Maquinaria = 1").fetchone()["m"]


def test_requiere_login_y_permiso(app, client, login, bd):
    assert client.get("/maquinaria/1/mantenciones/").status_code == 302
    assert client.get("/maquinaria/1/mantenciones/nueva").status_code == 302
    preparar(client, login)
    client.post("/logout")
    login("ger@x.cl", "Gerente123")
    assert client.get("/maquinaria/1/mantenciones/").status_code == 200       # solo lectura: puede ver
    assert client.get("/maquinaria/1/mantenciones/nueva").status_code == 302
    assert registrar(client, app).status_code == 302 and filas(bd) == []


def test_registrar_mantencion(app, client, login, bd):
    preparar(client, login)
    assert client.get("/maquinaria/1/mantenciones/nueva").status_code == 200
    r = registrar(client, app, 2, "1.472,5", "  Cambio de aceite\n  Filtro hidráulico  ")
    assert r.status_code == 302 and r.location.endswith("/maquinaria/1/mantenciones/")
    f = filas(bd)[0]
    assert (f["Horometro_Mecanico"], f["FK_ID_Usuario"]) == (1472.5, 1)
    assert f["Fecha_Mantencion"] == hoy_app(app) - timedelta(days=2)
    assert f["Detalle_Trabajo"] == "Cambio de aceite\n  Filtro hidráulico"        # conserva saltos de línea
    html = client.get("/maquinaria/1/mantenciones/").data.decode()
    assert "Cambio de aceite" in html and "1.472,50" in html and "Ana Test" in html


def test_texto_libre_sin_limite_restrictivo(app, client, login, bd):
    preparar(client, login)
    largo = ("Diagnóstico detallado del equipo: " + "x" * 40) * 600                # ~45.000 caracteres
    assert len(largo) > 40000 and registrar(client, app, detalle=largo).status_code == 302
    assert filas(bd)[0]["Detalle_Trabajo"] == largo
    html = client.get("/maquinaria/1/mantenciones/").data.decode()
    assert "Ver completo" in html                                                    # textos largos se pliegan
    enorme = "á" * 40000                                                              # 80.000 bytes: excede el TEXT
    assert registrar(client, app, 1, "1.001", enorme).status_code == 400
    assert len(filas(bd)) == 1


def test_validaciones(app, client, login, bd):
    preparar(client, login)
    assert registrar(client, app, lectura="").status_code == 400
    assert registrar(client, app, lectura="abc").status_code == 400
    assert registrar(client, app, lectura="-5").status_code == 400
    assert registrar(client, app, detalle="  ").status_code == 400
    assert registrar(client, app, detalle="ab").status_code == 400
    assert registrar(client, app, dias_atras=-1).status_code == 400                  # fecha futura
    assert client.post("/maquinaria/1/mantenciones/nueva", data={"fecha": "1999-01-01", "lectura": "1", "detalle": "xxx"}).status_code == 400
    assert client.post("/maquinaria/1/mantenciones/nueva", data={"fecha": "no", "lectura": "1", "detalle": "xxx"}).status_code == 400
    assert filas(bd) == []


def test_las_lecturas_no_pueden_retroceder(app, client, login, bd):
    preparar(client, login)
    assert registrar(client, app, 100, "1.000").status_code == 302
    assert registrar(client, app, 50, "1.500").status_code == 302
    assert registrar(client, app, 10, "1.200").status_code == 400                    # menor que la anterior (1.500)
    assert registrar(client, app, 10, "1.500").status_code == 302                    # igual: permitido
    assert registrar(client, app, 75, "1.250").status_code == 302                    # entre dos: permitido
    assert registrar(client, app, 75, "1.600").status_code == 400                    # mayor que la posterior
    assert registrar(client, app, 75, "900").status_code == 400                      # menor que la anterior
    assert len(filas(bd)) == 4
    assert registrar(client, app, 0, "1.500").status_code == 302                     # varias el mismo día
    assert registrar(client, app, 0, "1.500", "Segundo trabajo del día").status_code == 302


def test_programar_la_proxima_mantencion_actualiza_la_alerta(app, client, login, bd):
    preparar(client, login)
    assert meta(bd) is None
    r = registrar(client, app, 0, "1.500", intervalo="250")
    assert r.status_code == 302 and meta(bd) == 1750
    html = client.get("/maquinaria/1/mantenciones/").data.decode()
    assert "1.750,00 h" in html                                                      # banner de próxima mantención
    perfil = client.get("/maquinaria/1").data.decode()
    assert "1.750,00 h" in perfil


def test_no_se_programa_si_hay_una_mantencion_mas_reciente(app, client, login, bd):
    preparar(client, login)
    registrar(client, app, 0, "2.000", intervalo="250")
    assert meta(bd) == 2250
    registrar(client, app, 30, "1.500", intervalo="250")                              # retroactiva: no pisa la meta
    assert meta(bd) == 2250 and len(filas(bd)) == 2
    html = client.get("/maquinaria/1/mantenciones/").data.decode()
    assert "existe una mantención más reciente" in html


def test_intervalo_invalido_o_vacio(app, client, login, bd):
    preparar(client, login)
    assert registrar(client, app, 0, "1.000", intervalo="0").status_code == 400
    assert registrar(client, app, 0, "1.000", intervalo="-5").status_code == 400
    assert registrar(client, app, 0, "1.000", intervalo="xx").status_code == 400
    assert registrar(client, app, 0, "1.000", intervalo="").status_code == 302 and meta(bd) is None   # sin intervalo: no toca la meta
    assert len(filas(bd)) == 1


def test_la_alerta_de_mantencion_responde_al_registro(app, client, login, bd):
    """Integración con el RF5: la meta calculada alimenta el semáforo."""
    preparar(client, login)
    bd.execute("INSERT INTO bitacora_operativa(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Registro, Horometro, Estado_Diario) "
               "VALUES (1, 1, ?, 1530, 'Operativo')", (hoy_app(app).isoformat(),))
    bd.commit()
    registrar(client, app, 5, "1.500", intervalo="60")        # meta 1.560; horómetro actual 1.530 → faltan 30 h
    assert "Faltan 30,00 h" in client.get("/maquinaria/1").data.decode()


def test_editar_mantencion(app, client, login, bd):
    preparar(client, login)
    registrar(client, app, 10, "1.000")
    registrar(client, app, 5, "1.200", "Otro trabajo")
    assert client.get("/maquinaria/1/mantenciones/1/editar").status_code == 200
    nuevos = {"fecha": (hoy_app(app) - timedelta(days=9)).isoformat(), "lectura": "1.100,5", "detalle": "Cambio corregido"}
    assert client.post("/maquinaria/1/mantenciones/1/editar", data=nuevos).status_code == 302
    f = filas(bd)[0]
    assert (f["Horometro_Mecanico"], f["Detalle_Trabajo"]) == (1100.5, "Cambio corregido")
    assert client.post("/maquinaria/1/mantenciones/1/editar", data={**nuevos, "lectura": "1.300"}).status_code == 400   # superaría la posterior
    assert client.post("/maquinaria/1/mantenciones/1/editar", data={**nuevos, "detalle": ""}).status_code == 400
    assert meta(bd) is None                                                      # editar no toca la meta


def test_eliminar_mantencion(app, client, login, bd):
    preparar(client, login)
    registrar(client, app)
    assert client.get("/maquinaria/1/mantenciones/1/eliminar").status_code == 405
    assert client.post("/maquinaria/1/mantenciones/1/eliminar").status_code == 302 and filas(bd) == []
    assert client.post("/maquinaria/1/mantenciones/1/eliminar").status_code == 404


def test_un_registro_solo_se_alcanza_desde_su_maquina(app, client, login):
    preparar(client, login, n=2)
    registrar(client, app)
    assert client.get("/maquinaria/2/mantenciones/1/editar").status_code == 404
    assert client.post("/maquinaria/2/mantenciones/1/eliminar").status_code == 404
    assert client.get("/maquinaria/99/mantenciones/").status_code == 404


def test_busqueda_en_el_detalle_y_paginacion(app, client, login, bd):
    preparar(client, login)
    registrar(client, app, 30, "1.000", "Cambio de filtro hidráulico")
    registrar(client, app, 20, "1.100", "Reparación de neumático")
    client.get("/dashboard")                                                      # consume mensajes flash
    html = client.get("/maquinaria/1/mantenciones/?q=hidr").data.decode()
    assert "filtro hidráulico" in html and "neumático" not in html
    assert "No hay mantenciones que coincidan" in client.get("/maquinaria/1/mantenciones/?q=zzz").data.decode()
    for i in range(25):
        bd.execute("INSERT INTO registro_mantencion(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Mantencion, Horometro_Mecanico, "
                   "Detalle_Trabajo) VALUES (1, 1, ?, ?, ?)", ("2020-01-01", 10 + i, f"antiguo-{i:02d}"))
    bd.commit()
    p1 = client.get("/maquinaria/1/mantenciones/").data.decode()
    assert "Página 1 de 2" in p1 and "Cambio de filtro" in p1
    assert "antiguo-00" in client.get("/maquinaria/1/mantenciones/?pagina=2").data.decode()


def test_el_texto_se_escapa_en_pantalla(app, client, login):
    preparar(client, login)
    registrar(client, app, detalle="<script>alert(1)</script> trabajo")
    html = client.get("/maquinaria/1/mantenciones/").data.decode()
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_el_perfil_resume_las_mantenciones_y_bloquea_borrar_la_maquina(app, client, login, bd):
    preparar(client, login)
    assert "Sin mantenciones registradas" in client.get("/maquinaria/1").data.decode()
    registrar(client, app, 3, "1.234,5")
    html = client.get("/maquinaria/1").data.decode()
    assert "1 registro(s)" in html and "1.234,50" in html
    client.post("/maquinaria/1/eliminar")
    assert bd.execute("SELECT COUNT(*) AS n FROM maquinaria").fetchone()["n"] == 1
