from datetime import timedelta

from app.utils import ahora

MAQ = {"patente": "AB-1234", "modelo": "CAT 320", "anio": "2018", "combustible": "Diésel",
       "chasis": "CHS1", "estado": "Operativa"}


def ahora_str(app, dias=0):
    with app.app_context():
        return (ahora() - timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M")


def preparar(client, login, correo="ana@x.cl", clave="Correcta123"):
    login(correo, clave)
    client.post("/maquinaria/nueva", data=MAQ)
    client.post("/maquinaria/nueva", data={**MAQ, "patente": "CC-5555", "chasis": "CHS2"})


def cargar(client, app, maquina="1", litros="100", costo="100000", vale="", dias=0, **extra):
    datos = {"maquina": maquina, "fecha_carga": ahora_str(app, dias), "numero_vale": vale,
             "litros": litros, "costo_total": costo, **extra}
    return client.post("/combustible/nueva", data=datos)


def cargas(bd):
    return bd.execute("SELECT * FROM carga_combustible ORDER BY ID_Carga").fetchall()


def test_requiere_login(client):
    for ruta in ("/combustible/", "/combustible/nueva", "/combustible/exportar.csv"):
        assert client.get(ruta).status_code == 302


def test_registrar_carga(app, client, login, bd):
    preparar(client, login)
    r = cargar(client, app, litros="120,5", costo="125.000", vale="V-001")
    assert r.status_code == 302 and "/combustible/" in r.location
    c = cargas(bd)[0]
    assert (c["FK_ID_Maquinaria"], c["FK_ID_Usuario"], c["Numero_Vale"]) == (1, 1, "V-001")
    assert (c["Litros_Cargados"], c["Costo_Total"]) == (120.5, 125000)
    assert b"V-001" in client.get(r.location).data       # la carga aparece tras guardar


def test_formatos_numericos_chilenos(app, client, login, bd):
    preparar(client, login)
    for litros, costo, vale in (("80.5", "$85000", "a"), ("1.250,5", "1.234.567,89", "b"), ("60", "85.000", "c")):
        assert cargar(client, app, litros=litros, costo=costo, vale=vale).status_code == 302, (litros, costo)
    assert [(c["Litros_Cargados"], c["Costo_Total"]) for c in cargas(bd)] == \
           [(80.5, 85000), (1250.5, 1234567.89), (60, 85000)]


def test_validaciones(app, client, login, bd):
    preparar(client, login)
    malos = [{"litros": "0"}, {"litros": "-5"}, {"litros": "5001"}, {"litros": "abc"}, {"litros": ""},
             {"costo": "-1"}, {"costo": "xyz"}, {"costo": ""}, {"maquina": "99"}, {"maquina": ""},
             {"vale": "x" * 51}, {"fecha_carga": "no-es-fecha"}, {"fecha_carga": ""}]
    for cambio in malos:
        assert cargar(client, app, **cambio).status_code == 400, cambio
    futura = {"fecha_carga": ahora_str(app, -2)}
    assert cargar(client, app, **futura).status_code == 400
    assert cargas(bd) == []


def test_vale_duplicado_solo_dentro_de_la_misma_maquina(app, client, login, bd):
    preparar(client, login)
    assert cargar(client, app, vale="V-9").status_code == 302
    assert cargar(client, app, vale="V-9").status_code == 400                    # misma máquina
    assert cargar(client, app, maquina="2", vale="V-9").status_code == 302       # otra máquina
    assert cargar(client, app, vale="").status_code == 302                       # sin vale se puede repetir
    assert cargar(client, app, vale="").status_code == 302
    assert len(cargas(bd)) == 4


def test_cuadratura_totales_y_por_maquina(app, client, login):
    preparar(client, login)
    cargar(client, app, "1", "100", "100000", "a")
    cargar(client, app, "1", "50", "55000", "b")
    cargar(client, app, "2", "200", "180000", "c")
    html = client.get("/combustible/").data.decode()
    assert "350,00 L" in html and "$335.000" in html and "$957" in html          # totales y $/litro
    assert "150,00" in html and "$155.000" in html and "$1.033" in html          # máquina 1
    assert "200,00" in html and "$180.000" in html and "$900" in html            # máquina 2
    solo1 = client.get("/combustible/?maquina=1").data.decode()
    assert "150,00 L" in solo1 and "$155.000" in solo1 and "$180.000" not in solo1


def test_periodo_por_defecto_es_el_mes_y_se_puede_ampliar(app, client, login):
    preparar(client, login)
    cargar(client, app, vale="actual")
    cargar(client, app, vale="antigua", dias=90)
    mes = client.get("/combustible/").data.decode()
    assert "actual" in mes and "antigua" not in mes
    todo = client.get("/combustible/?desde=&hasta=").data.decode()
    assert "actual" in todo and "antigua" in todo
    with app.app_context():
        d = (ahora() - timedelta(days=91)).date().isoformat()
        h = (ahora() - timedelta(days=89)).date().isoformat()
    rango = client.get(f"/combustible/?desde={d}&hasta={h}").data.decode()
    assert "antigua" in rango and "actual" not in rango


def test_paginacion(app, client, login, bd):
    preparar(client, login)
    with app.app_context():
        base = ahora()
    for i in range(55):
        bd.execute("INSERT INTO carga_combustible(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Carga, Numero_Vale, "
                   "Litros_Cargados, Costo_Total) VALUES (1, 1, ?, ?, 10, 1000)",
                   (base - timedelta(minutes=i + 1), f"vale{i}"))
    bd.commit()
    html = client.get("/combustible/").data.decode()
    assert "Página 1 de 2" in html and "55" in html and "vale0" in html and "vale54" not in html
    assert "vale54" in client.get("/combustible/?pagina=2").data.decode()


def test_exportar_csv(app, client, login):
    preparar(client, login)
    cargar(client, app, litros="120,5", costo="125.000", vale="=HYPERLINK(\"x\")")
    r = client.get("/combustible/exportar.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"] and ".csv" in r.headers["Content-Disposition"]
    texto = r.data.decode("utf-8-sig")
    lineas = texto.strip().splitlines()
    assert lineas[0] == "Fecha;Patente;Modelo;Vale;Litros;Costo total;Costo por litro;Registrado por"
    campos = lineas[1].split(";")
    assert campos[1] == "AB-1234" and campos[4] == "120,50" and campos[5] == "125000,00"
    assert "'=HYPERLINK" in texto                           # sin inyección de fórmulas
    assert r.data.startswith(b"\xef\xbb\xbf")               # BOM para que Excel lea las tildes


def test_editar_carga(app, client, login, bd):
    preparar(client, login)
    cargar(client, app, vale="V1")
    assert client.get("/combustible/1/editar").status_code == 200
    datos = {"maquina": "2", "fecha_carga": ahora_str(app), "numero_vale": "V1", "litros": "90", "costo_total": "99.000"}
    assert client.post("/combustible/1/editar", data=datos).status_code == 302
    c = cargas(bd)[0]
    assert (c["FK_ID_Maquinaria"], c["Litros_Cargados"], c["Costo_Total"]) == (2, 90, 99000)
    # el propio vale no cuenta como duplicado; uno ajeno sí
    cargar(client, app, vale="OTRO")
    assert client.post("/combustible/1/editar", data={**datos, "numero_vale": "V1"}).status_code == 302
    assert client.post("/combustible/2/editar", data={**datos, "maquina": "2", "numero_vale": "V1"}).status_code == 400
    assert client.post("/combustible/1/editar", data={**datos, "litros": "0"}).status_code == 400


def test_eliminar_carga(app, client, login, bd):
    preparar(client, login)
    cargar(client, app)
    assert client.get("/combustible/1/eliminar").status_code == 405
    assert client.post("/combustible/1/eliminar").status_code == 302 and cargas(bd) == []
    assert client.post("/combustible/1/eliminar").status_code == 404


def test_rol_de_solo_lectura(app, client, login, bd):
    preparar(client, login)
    cargar(client, app, vale="V1")
    client.post("/logout")
    login("ger@x.cl", "Gerente123")
    assert client.get("/combustible/").status_code == 200
    assert client.get("/combustible/exportar.csv").status_code == 200
    html = client.get("/combustible/").data.decode()
    assert "Registrar carga" not in html and "Eliminar" not in html
    assert client.get("/combustible/nueva").status_code == 302
    assert cargar(client, app, vale="V2").status_code == 302 and len(cargas(bd)) == 1
    assert client.post("/combustible/1/eliminar").status_code == 302 and len(cargas(bd)) == 1
    assert client.get("/combustible/1/editar").status_code == 302


def test_perfil_muestra_ultima_carga_y_maquina_con_cargas_no_se_elimina(app, client, login, bd):
    preparar(client, login)
    assert "Sin cargas registradas" in client.get("/maquinaria/1").data.decode()
    cargar(client, app, litros="77,25")
    assert "77,25 L" in client.get("/maquinaria/1").data.decode()
    client.post("/maquinaria/1/eliminar")
    assert bd.execute("SELECT COUNT(*) AS n FROM maquinaria WHERE ID_Maquinaria = 1").fetchone()["n"] == 1
