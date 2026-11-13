from io import BytesIO

from PIL import Image

DATOS = {"patente": "ab-1234", "modelo": "Caterpillar 320", "anio": "2018",
         "combustible": "Diésel", "chasis": "chs0001", "estado": "Operativa"}


def png():
    buf = BytesIO()
    Image.new("RGB", (3000, 2000), "red").save(buf, "PNG")
    buf.seek(0)
    return buf


def crear(client, **cambios):
    datos = {**DATOS, **cambios}
    return client.post("/maquinaria/nueva", data=datos, content_type="multipart/form-data")


def contar(bd, tabla="maquinaria"):
    return bd.execute(f"SELECT COUNT(*) AS n FROM {tabla}").fetchone()["n"]


def test_requiere_login(client):
    assert client.get("/maquinaria/").status_code == 302
    assert client.get("/maquinaria/nueva").status_code == 302


def test_crear_maquinaria_valida(client, login, bd):
    login("ana@x.cl", "Correcta123")
    r = crear(client)
    assert r.status_code == 302 and r.location.endswith("/maquinaria/1")
    m = bd.execute("SELECT * FROM maquinaria").fetchone()
    assert m["Patente"] == "AB-1234" and m["Numero_Chasis"] == "CHS0001"      # normalizados a mayúsculas
    assert contar(bd, "alerta_estatica") == 1                                  # panel 1 a 1 creado
    r = client.get("/maquinaria/1")
    assert r.status_code == 200 and b"AB-1234" in r.data and b"Operativa" in r.data


def test_validaciones(client, login, bd):
    login("ana@x.cl", "Correcta123")
    for cambio in ({"patente": "!!"}, {"modelo": ""}, {"anio": "1800"}, {"anio": "abc"},
                   {"combustible": "Agua"}, {"chasis": ""}, {"estado": "Rota"}):
        assert crear(client, **cambio).status_code == 400, cambio
    assert contar(bd) == 0


def test_duplicados(client, login, bd):
    login("ana@x.cl", "Correcta123")
    assert crear(client).status_code == 302
    assert crear(client, chasis="OTRO").status_code == 400           # patente repetida
    assert crear(client, patente="ZZ-9999").status_code == 400       # chasis repetido
    assert contar(bd) == 1


def test_foto_se_guarda_reducida_y_se_sirve(client, login, bd, app):
    login("ana@x.cl", "Correcta123")
    assert crear(client, foto=(png(), "foto.png")).status_code == 302
    ruta = bd.execute("SELECT Fotografia_Principal FROM maquinaria").fetchone()["Fotografia_Principal"]
    archivo = app.config["UPLOAD_FOLDER"] / ruta
    assert archivo.exists() and Image.open(archivo).size[0] <= 1600
    r = client.get("/maquinaria/1/foto")
    assert r.status_code == 200 and r.mimetype == "image/jpeg"


def test_foto_invalida_rechazada(client, login, bd):
    login("ana@x.cl", "Correcta123")
    r = crear(client, foto=(BytesIO(b"esto no es una imagen"), "virus.png"))
    assert r.status_code == 400 and contar(bd) == 0


def test_foto_requiere_login(client, login):
    login("ana@x.cl", "Correcta123")
    crear(client, foto=(png(), "f.png"))
    client.post("/logout")
    assert client.get("/maquinaria/1/foto").status_code == 302


def test_editar_solo_estado_y_foto_datos_base_inmutables(client, login, bd):
    login("ana@x.cl", "Correcta123")
    crear(client)
    r = client.post("/maquinaria/1/editar", data={"estado": "En taller/Mantención", "patente": "HACK-1",
                                                  "modelo": "Otro", "anio": "1999"})
    assert r.status_code == 302
    m = bd.execute("SELECT * FROM maquinaria").fetchone()
    assert m["Estado_Operativo_Actual"] == "En taller/Mantención"
    assert (m["Patente"], m["Modelo"], m["Anio"]) == ("AB-1234", "Caterpillar 320", 2018)


def test_editar_cambia_y_quita_foto(client, login, bd, app):
    login("ana@x.cl", "Correcta123")
    crear(client, foto=(png(), "f.png"))
    vieja = bd.execute("SELECT Fotografia_Principal FROM maquinaria").fetchone()["Fotografia_Principal"]
    client.post("/maquinaria/1/editar", data={"estado": "Operativa", "foto": (png(), "n.png")},
                content_type="multipart/form-data")
    nueva = bd.execute("SELECT Fotografia_Principal FROM maquinaria").fetchone()["Fotografia_Principal"]
    assert nueva != vieja and not (app.config["UPLOAD_FOLDER"] / vieja).exists()
    client.post("/maquinaria/1/editar", data={"estado": "Operativa", "quitar_foto": "1"})
    assert bd.execute("SELECT Fotografia_Principal FROM maquinaria").fetchone()["Fotografia_Principal"] is None
    assert not (app.config["UPLOAD_FOLDER"] / nueva).exists()


def test_eliminar_sin_historial(client, login, bd, app):
    login("ana@x.cl", "Correcta123")
    crear(client, foto=(png(), "f.png"))
    ruta = bd.execute("SELECT Fotografia_Principal FROM maquinaria").fetchone()["Fotografia_Principal"]
    r = client.post("/maquinaria/1/eliminar")
    assert r.status_code == 302 and r.location.endswith("/maquinaria/")
    assert contar(bd) == 0 and contar(bd, "alerta_estatica") == 0
    assert not (app.config["UPLOAD_FOLDER"] / ruta).exists()


def test_no_se_elimina_con_historial(client, login, bd):
    login("ana@x.cl", "Correcta123")
    crear(client)
    bd.execute("INSERT INTO bitacora_operativa(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Registro, Estado_Diario) "
               "VALUES (1, 1, '2026-01-01', 'Operativo')")
    bd.commit()
    client.post("/maquinaria/1/eliminar")
    assert contar(bd) == 1


def test_rol_de_solo_lectura_no_puede_escribir(client, login, bd):
    login("ana@x.cl", "Correcta123")
    crear(client)
    client.post("/logout")
    login("ger@x.cl", "Gerente123")
    assert client.get("/maquinaria/").status_code == 200
    assert client.get("/maquinaria/1").status_code == 200
    assert b"Nueva maquinaria" not in client.get("/maquinaria/").data
    assert client.get("/maquinaria/nueva").status_code == 302
    assert crear(client, patente="ZZ-1111", chasis="ZZ1").status_code == 302 and contar(bd) == 1
    assert client.post("/maquinaria/1/eliminar").status_code == 302 and contar(bd) == 1
    assert client.post("/maquinaria/1/editar", data={"estado": "Inactiva"}).status_code == 302
    assert bd.execute("SELECT Estado_Operativo_Actual AS e FROM maquinaria").fetchone()["e"] == "Operativa"


def test_busqueda_y_filtro(client, login):
    login("ana@x.cl", "Correcta123")
    crear(client)
    crear(client, patente="CC-5555", chasis="C2", modelo="Komatsu", estado="Inactiva")
    assert b"AB-1234" in client.get("/maquinaria/?q=cater").data
    assert b"CC-5555" not in client.get("/maquinaria/?q=cater").data
    r = client.get("/maquinaria/?estado=Inactiva")
    assert b"CC-5555" in r.data and b"AB-1234" not in r.data


def test_404_si_no_existe(client, login):
    login("ana@x.cl", "Correcta123")
    assert client.get("/maquinaria/99").status_code == 404


def test_dashboard_muestra_resumen(client, login):
    login("ana@x.cl", "Correcta123")
    crear(client)
    crear(client, patente="CC-5555", chasis="C2", estado="Inactiva")
    r = client.get("/dashboard")
    assert r.status_code == 200 and b"Total de la flota" in r.data
