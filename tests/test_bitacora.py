from datetime import timedelta

from app.utils import ahora

MAQUINA = {"patente": "AB-1234", "modelo": "CAT 320", "anio": "2018", "combustible": "Diésel",
           "chasis": "CHS1", "estado": "Operativa"}


def hoy(app):
    with app.app_context():
        return ahora().date()


def preparar(client, login, correo="ana@x.cl", clave="Correcta123"):
    login(correo, clave)
    client.post("/maquinaria/nueva", data=MAQUINA)


def registrar(client, fecha, **campos):
    datos = {"fecha": fecha.isoformat(), "horometro": "", "kilometraje": "", "estado_diario": "Operativo"}
    datos.update(campos)
    return client.post("/maquinaria/1/bitacora/nuevo", data=datos)


def filas(bd):
    return bd.execute("SELECT * FROM bitacora_operativa ORDER BY Fecha_Registro").fetchall()


def test_requiere_login(client):
    assert client.get("/maquinaria/1/bitacora/").status_code == 302
    assert client.get("/maquinaria/1/bitacora/nuevo").status_code == 302


def test_registro_con_lecturas(app, client, login, bd):
    preparar(client, login)
    r = registrar(client, hoy(app), horometro="1250,5", kilometraje="30000", estado_diario="Operativo")
    assert r.status_code == 302 and r.location.endswith("/maquinaria/1/bitacora/")
    f = filas(bd)[0]
    assert (f["Horometro"], f["Kilometraje"], f["FK_ID_Usuario"]) == (1250.5, 30000, 1)
    assert f["Fecha_Hora_Ingreso"] is not None
    html = client.get("/maquinaria/1/bitacora/").data
    assert b"1250.50" in html and b"Ana Test" in html


def test_dia_sin_actividad_no_exige_lecturas(app, client, login, bd):
    preparar(client, login)
    assert registrar(client, hoy(app), estado_diario="Día en taller").status_code == 302
    f = filas(bd)[0]
    assert f["Horometro"] is None and f["Kilometraje"] is None and f["Estado_Diario"] == "Día en taller"


def test_validaciones_basicas(app, client, login, bd):
    preparar(client, login)
    h = hoy(app)
    assert registrar(client, h + timedelta(days=1)).status_code == 400            # fecha futura
    assert registrar(client, h, estado_diario="  ").status_code == 400            # estado vacío
    assert registrar(client, h, horometro="abc").status_code == 400               # no numérico
    assert registrar(client, h, horometro="-5").status_code == 400                # negativo
    assert registrar(client, h, kilometraje="1e999999").status_code == 400        # fuera de rango
    assert registrar(client, h, estado_diario="x" * 256).status_code == 400
    r = client.post("/maquinaria/1/bitacora/nuevo", data={"fecha": "no-es-fecha", "estado_diario": "x"})
    assert r.status_code == 400
    assert filas(bd) == []


def test_un_solo_registro_por_dia(app, client, login, bd):
    preparar(client, login)
    h = hoy(app)
    assert registrar(client, h, horometro="100").status_code == 302
    r = registrar(client, h, horometro="200", estado_diario="Otro")
    assert r.status_code == 400 and len(filas(bd)) == 1 and filas(bd)[0]["Horometro"] == 100


def test_lecturas_no_pueden_retroceder(app, client, login, bd):
    preparar(client, login)
    h = hoy(app)
    assert registrar(client, h - timedelta(days=4), horometro="100", kilometraje="1000").status_code == 302
    assert registrar(client, h - timedelta(days=2), horometro="200", kilometraje="2000").status_code == 302
    # retroceso respecto del anterior
    assert registrar(client, h, horometro="50").status_code == 400
    assert registrar(client, h, kilometraje="999").status_code == 400
    # igual al anterior (sin avance) sí se permite
    assert registrar(client, h, horometro="200").status_code == 302
    # carga retroactiva entre dos registros: debe quedar entre ambos
    d3 = h - timedelta(days=3)
    assert registrar(client, d3, horometro="250").status_code == 400
    assert registrar(client, d3, horometro="50").status_code == 400
    assert registrar(client, d3, horometro="150", kilometraje="1500").status_code == 302
    assert len(filas(bd)) == 4


def test_bitacora_es_inalterable_no_hay_rutas_de_edicion(app, client, login, bd):
    preparar(client, login)
    registrar(client, hoy(app), horometro="10")
    for ruta in ("/maquinaria/1/bitacora/1/editar", "/maquinaria/1/bitacora/1/eliminar", "/maquinaria/1/bitacora/1"):
        assert client.post(ruta, data={"horometro": "1"}).status_code in (404, 405)
        assert client.delete(ruta).status_code in (404, 405)
    reglas = [r for r in app.url_map.iter_rules() if r.endpoint.startswith("bitacora.")]
    assert {r.endpoint for r in reglas} == {"bitacora.lista", "bitacora.nuevo"}
    assert filas(bd)[0]["Horometro"] == 10


def test_rol_de_solo_lectura(app, client, login, bd):
    preparar(client, login)
    registrar(client, hoy(app), horometro="10")
    client.post("/logout")
    login("ger@x.cl", "Gerente123")
    assert client.get("/maquinaria/1/bitacora/").status_code == 200
    assert b"Registrar d" not in client.get("/maquinaria/1/bitacora/").data
    assert client.get("/maquinaria/1/bitacora/nuevo").status_code == 302
    assert registrar(client, hoy(app) - timedelta(days=1), horometro="5").status_code == 302
    assert len(filas(bd)) == 1


def test_dias_sin_registro_y_enlace_para_completarlos(app, client, login):
    preparar(client, login)
    h = hoy(app)
    registrar(client, h - timedelta(days=5), horometro="10")
    registrar(client, h - timedelta(days=2), horometro="20")
    registrar(client, h, horometro="30")
    html = client.get("/maquinaria/1/bitacora/").data.decode()
    # faltan hace 4, 3 y 1 días (hoy no cuenta)
    assert "3 día(s) sin registro" in html
    assert f"fecha={(h - timedelta(days=3)).isoformat()}" in html
    form = client.get(f"/maquinaria/1/bitacora/nuevo?fecha={(h - timedelta(days=3)).isoformat()}").data.decode()
    assert (h - timedelta(days=3)).isoformat() in form


def test_sin_registros_no_hay_aviso(client, login):
    preparar(client, login)
    r = client.get("/maquinaria/1/bitacora/")
    assert r.status_code == 200 and "sin registro" not in r.data.decode().replace("sin registro de", "")
    assert b"No hay registros" in r.data


def test_filtro_por_fechas_y_paginacion(app, client, login, bd):
    preparar(client, login)
    h = hoy(app)
    for i in range(35):
        bd.execute("INSERT INTO bitacora_operativa(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Registro, Estado_Diario) "
                   "VALUES (1, 1, ?, ?)", ((h - timedelta(days=i + 1)).isoformat(), f"dia{i}"))
    bd.commit()
    p1 = client.get("/maquinaria/1/bitacora/").data.decode()
    assert "página 1 de 2" in p1 and "dia0" in p1 and "dia34" not in p1
    assert "dia34" in client.get("/maquinaria/1/bitacora/?pagina=2").data.decode()
    desde = (h - timedelta(days=3)).isoformat()
    r = client.get(f"/maquinaria/1/bitacora/?desde={desde}").data.decode()
    assert "dia0" in r and "dia2" in r and "dia3" not in r


def test_perfil_muestra_ultimas_lecturas(app, client, login):
    preparar(client, login)
    registrar(client, hoy(app), horometro="321,25", kilometraje="9000")
    html = client.get("/maquinaria/1").data.decode()
    assert "321.25 h" in html and "9000.00 km" in html


def test_maquina_inexistente(client, login):
    login("ana@x.cl", "Correcta123")
    assert client.get("/maquinaria/99/bitacora/").status_code == 404
    assert client.get("/maquinaria/99/bitacora/nuevo").status_code == 404


def test_formato_numerico_chileno_en_lecturas(app, client, login, bd):
    """Regresión: «1.250» debe ser 1250 (separador de miles), no 1,25."""
    preparar(client, login)
    h = hoy(app)
    assert registrar(client, h - timedelta(days=2), horometro="1.250", kilometraje="30.000,5").status_code == 302
    assert registrar(client, h - timedelta(days=1), horometro="1.250,5").status_code == 302
    assert registrar(client, h, horometro="1251.25").status_code == 302
    assert [(f["Horometro"], f["Kilometraje"]) for f in filas(bd)] == [(1250, 30000.5), (1250.5, None), (1251.25, None)]

# --------------------------------------------------------------------------
# Parte diario de equipo (v2.3)
# --------------------------------------------------------------------------
def test_parte_diario_completo(app, client, login, bd):
    preparar(client, login)
    r = registrar(client, hoy(app), numero_parte="007429", faena="Ruta 5 km 340", operador="Juan Pérez",
                  horometro_inicio="1.200,5", horometro="1208,75", turno="Día", hora_comienzo="08:00",
                  hora_termino="18:30", tipo_trabajo="Carguío de áridos", observaciones="Sin novedad",
                  petroleo="120,5", jefe_obras="Pedro Soto")
    assert r.status_code == 302
    f = filas(bd)[0]
    assert (f["Numero_Parte"], f["Faena"], f["Operador"], f["Turno"], f["Jefe_Obras"]) == \
        ("007429", "Ruta 5 km 340", "Juan Pérez", "Día", "Pedro Soto")
    assert (f["Horometro_Inicio"], f["Horometro"], f["Horas_Trabajadas"], f["Petroleo_Litros"]) == \
        (1200.5, 1208.75, 8.25, 120.5)
    assert (f["Hora_Comienzo"], f["Hora_Termino"]) == ("08:00:00", "18:30:00")
    html = client.get("/maquinaria/1/bitacora/").data.decode()
    assert "N° 007429" in html and "Juan Pérez" in html and "8.25" in html and "08:00 a 18:30" in html


def test_parte_sin_campos_nuevos_sigue_funcionando(app, client, login, bd):
    preparar(client, login)
    assert registrar(client, hoy(app), horometro="100").status_code == 302
    f = filas(bd)[0]
    assert f["Horometro_Inicio"] is None and f["Horas_Trabajadas"] is None and f["Faena"] is None


def test_inicio_mayor_que_final_rechazado(app, client, login, bd):
    preparar(client, login)
    r = registrar(client, hoy(app), horometro_inicio="200", horometro="150")
    assert r.status_code == 400 and filas(bd) == []
    r = registrar(client, hoy(app), horometro_inicio="200")
    assert r.status_code == 400 and "indica también el horómetro final".encode() in r.data


def test_inicio_no_puede_bajar_del_final_anterior(app, client, login, bd):
    preparar(client, login)
    h = hoy(app)
    assert registrar(client, h - timedelta(days=1), horometro="500").status_code == 302
    r = registrar(client, h, horometro_inicio="490", horometro="510")
    assert r.status_code == 400 and len(filas(bd)) == 1


def test_final_no_puede_superar_inicio_posterior(app, client, login, bd):
    preparar(client, login)
    h = hoy(app)
    assert registrar(client, h, horometro_inicio="600", horometro="610").status_code == 302
    r = registrar(client, h - timedelta(days=1), horometro="605")
    assert r.status_code == 400 and len(filas(bd)) == 1


def test_numero_parte_duplicado_y_hora_invalida(app, client, login, bd):
    preparar(client, login)
    h = hoy(app)
    assert registrar(client, h - timedelta(days=1), numero_parte="100").status_code == 302
    r = registrar(client, h, numero_parte="100")
    assert r.status_code == 400 and "ya está registrado".encode() in r.data
    r = registrar(client, h, hora_comienzo="25:99")
    assert r.status_code == 400 and len(filas(bd)) == 1


def test_formulario_sugiere_horometro_inicio(app, client, login, bd):
    preparar(client, login)
    registrar(client, hoy(app) - timedelta(days=1), horometro="1250,5")
    html = client.get("/maquinaria/1/bitacora/nuevo").data.decode()
    assert 'value="1250,50"' in html and "Parte diario de equipo" in html
