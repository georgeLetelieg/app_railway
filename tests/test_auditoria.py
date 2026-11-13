import json
import re
from datetime import timedelta

import pytest

from app.utils import ahora

MAQ = {"patente": "AB-1230", "modelo": "CAT 320", "anio": "2018", "combustible": "Diésel",
       "chasis": "CHS0", "estado": "Operativa"}


def hoy(app):
    with app.app_context():
        return ahora().date()


def ahora_str(app):
    with app.app_context():
        return ahora().strftime("%Y-%m-%dT%H:%M")


def entrar(client, login, quien="ana"):
    cuentas = {"ana": ("ana@x.cl", "Correcta123"), "ger": ("ger@x.cl", "Gerente123"), "sa": ("sa@x.cl", "Super12345")}
    client.post("/logout")
    login(*cuentas[quien])


def preparar(client, login, n=1):
    entrar(client, login, "ana")
    for i in range(n):
        client.post("/maquinaria/nueva", data={**MAQ, "patente": f"AB-123{i}", "chasis": f"CHS{i}"})


def cargar(client, app, litros="100", costo="100000", vale="V1", maquina="1"):
    return client.post("/combustible/nueva", data={"maquina": maquina, "fecha_carga": ahora_str(app), "numero_vale": vale,
                                                   "litros": litros, "costo_total": costo})


def mantencion(client, app, lectura="1.000", detalle="Cambio de aceite", dias=0):
    return client.post("/maquinaria/1/mantenciones/nueva",
                       data={"fecha": (hoy(app) - timedelta(days=dias)).isoformat(), "lectura": lectura, "detalle": detalle})


def log(bd):
    return bd.execute("SELECT * FROM auditoria ORDER BY ID_Auditoria").fetchall()


def datos(fila, campo="Datos_Anteriores"):
    return json.loads(fila[campo]) if fila[campo] else None


# ---------------- combustible ----------------
def test_crear_no_deja_registro_pero_corregir_si(app, client, login, bd):
    preparar(client, login)
    cargar(client, app)
    assert log(bd) == []                                              # crear no se audita
    r = client.post("/combustible/1/editar", data={"maquina": "1", "fecha_carga": ahora_str(app), "numero_vale": "V1",
                                                   "litros": "90", "costo_total": "99.000"})
    assert r.status_code == 302
    (a,) = log(bd)
    assert (a["Tabla_Afectada"], a["ID_Registro"], a["Accion"], a["FK_ID_Usuario"], a["FK_ID_Maquinaria"]) == \
           ("carga_combustible", 1, "Modificación", 1, 1)
    assert datos(a)["Litros"] == "100.00" and datos(a, "Datos_Nuevos")["Litros"] == "90.00"
    assert datos(a)["Costo total"] == "100000.00" and datos(a, "Datos_Nuevos")["Costo total"] == "99000.00"
    assert a["Fecha_Hora"] is not None


def test_guardar_sin_cambios_no_deja_registro(app, client, login, bd):
    preparar(client, login)
    cargar(client, app)
    same = {"maquina": "1", "fecha_carga": ahora_str(app), "numero_vale": "V1", "litros": "100", "costo_total": "100.000"}
    assert client.post("/combustible/1/editar", data=same).status_code == 302
    assert log(bd) == []


def test_cambiar_de_maquina_queda_registrado(app, client, login, bd):
    preparar(client, login, n=2)
    cargar(client, app)
    client.post("/combustible/1/editar", data={"maquina": "2", "fecha_carga": ahora_str(app), "numero_vale": "V1",
                                               "litros": "100", "costo_total": "100000"})
    (a,) = log(bd)
    assert datos(a)["Maquinaria"] == "AB-1230" and datos(a, "Datos_Nuevos")["Maquinaria"] == "AB-1231"
    assert a["FK_ID_Maquinaria"] == 2


def test_eliminar_carga_deja_registro_completo(app, client, login, bd):
    preparar(client, login)
    cargar(client, app, litros="55,5", costo="70.000", vale="X-9")
    assert client.post("/combustible/1/eliminar").status_code == 302
    (a,) = log(bd)
    assert (a["Accion"], a["Tabla_Afectada"], a["ID_Registro"], a["FK_ID_Maquinaria"]) == ("Eliminación", "carga_combustible", 1, 1)
    assert datos(a)["Litros"] == "55.50" and datos(a)["N° de vale"] == "X-9" and datos(a)["Maquinaria"] == "AB-1230"
    assert a["Datos_Nuevos"] is None
    assert bd.execute("SELECT COUNT(*) AS n FROM carga_combustible").fetchone()["n"] == 0


def test_el_cambio_y_su_registro_se_guardan_juntos_o_ninguno(app, client, login, bd, monkeypatch):
    preparar(client, login)
    cargar(client, app)
    import app.combustible.routes as rutas

    def falla(*a, **k):
        raise RuntimeError("falla simulada al auditar")
    monkeypatch.setattr(rutas, "registrar", falla)
    with pytest.raises(RuntimeError):
        client.post("/combustible/1/editar", data={"maquina": "1", "fecha_carga": ahora_str(app), "numero_vale": "V1",
                                                   "litros": "1", "costo_total": "1"})
    with pytest.raises(RuntimeError):
        client.post("/combustible/1/eliminar")
    fila = bd.execute("SELECT Litros_Cargados FROM carga_combustible").fetchone()
    assert fila["Litros_Cargados"] == 100 and log(bd) == []           # nada cambió ni se borró


# ---------------- mantenciones ----------------
def test_editar_y_eliminar_mantencion(app, client, login, bd):
    preparar(client, login)
    mantencion(client, app)
    nuevo = {"fecha": hoy(app).isoformat(), "lectura": "1.050", "detalle": "Cambio de aceite y filtros"}
    assert client.post("/maquinaria/1/mantenciones/1/editar", data=nuevo).status_code == 302
    assert client.post("/maquinaria/1/mantenciones/1/editar", data=nuevo).status_code == 302   # sin cambios: no registra
    assert len(log(bd)) == 1
    a = log(bd)[0]
    assert (a["Tabla_Afectada"], a["Accion"], a["FK_ID_Maquinaria"]) == ("registro_mantencion", "Modificación", 1)
    assert datos(a)["Lectura"] == "1000.00" and datos(a, "Datos_Nuevos")["Lectura"] == "1050.00"
    assert datos(a)["Detalle"] == "Cambio de aceite" and datos(a, "Datos_Nuevos")["Detalle"] == "Cambio de aceite y filtros"
    assert client.post("/maquinaria/1/mantenciones/1/eliminar").status_code == 302
    e = log(bd)[1]
    assert e["Accion"] == "Eliminación" and datos(e)["Detalle"] == "Cambio de aceite y filtros" and e["Datos_Nuevos"] is None


def test_textos_largos_se_conservan_completos_en_la_auditoria(app, client, login, bd):
    preparar(client, login)
    largo = "Diagnóstico extenso " * 2000                              # ~40.000 caracteres
    mantencion(client, app, detalle=largo)
    client.post("/maquinaria/1/mantenciones/1/eliminar")
    assert datos(log(bd)[0])["Detalle"] == largo.strip()


# ---------------- corrección de datos base (Super Administrador) ----------------
def corregir(client, **cambios):
    datos_ = {**MAQ, "patente": "AB-1230", "chasis": "CHS0", "estado": "Operativa", **cambios}
    return client.post("/maquinaria/1/editar", data=datos_)


def test_super_admin_puede_corregir_datos_base_y_queda_registrado(app, client, login, bd):
    preparar(client, login)
    entrar(client, login, "sa")
    r = corregir(client, patente="zz-9999", modelo="Komatsu PC200", anio="2020", combustible="Gasolina", chasis="nuevo-chasis")
    assert r.status_code == 302
    m = bd.execute("SELECT * FROM maquinaria").fetchone()
    assert (m["Patente"], m["Modelo"], m["Anio"], m["Tipo_Combustible"], m["Numero_Chasis"]) == \
           ("ZZ-9999", "Komatsu PC200", 2020, "Gasolina", "NUEVO-CHASIS")
    (a,) = log(bd)
    assert (a["Tabla_Afectada"], a["Accion"], a["FK_ID_Maquinaria"]) == ("maquinaria", "Modificación", 1)
    assert datos(a)["Patente"] == "AB-1230" and datos(a, "Datos_Nuevos")["Patente"] == "ZZ-9999"
    assert datos(a)["Año"] == "2018" and datos(a, "Datos_Nuevos")["Año"] == "2020"


def test_el_formulario_muestra_campos_editables_solo_al_super_admin(client, login):
    preparar(client, login)
    html = client.get("/maquinaria/1/editar").data.decode()                       # Administrativo
    assert 'name="patente"' not in html and "inmutables" in html
    entrar(client, login, "sa")
    html = client.get("/maquinaria/1/editar").data.decode()
    assert 'name="patente"' in html and 'value="AB-1230"' in html and "registrado en la auditoría" in html


def test_otros_roles_no_pueden_corregir_datos_base(client, login, bd):
    preparar(client, login)
    assert corregir(client, patente="HACK-1", modelo="X").status_code == 302       # Administrativo: se ignoran
    m = bd.execute("SELECT Patente, Modelo FROM maquinaria").fetchone()
    assert (m["Patente"], m["Modelo"]) == ("AB-1230", "CAT 320") and log(bd) == []
    entrar(client, login, "ger")
    assert client.get("/maquinaria/1/editar").status_code == 302                    # Gerencia: solo lectura
    assert corregir(client, patente="HACK-2").status_code == 302
    assert bd.execute("SELECT Patente FROM maquinaria").fetchone()["Patente"] == "AB-1230"


def test_la_correccion_valida_y_rechaza_duplicados(client, login, bd):
    preparar(client, login, n=2)
    entrar(client, login, "sa")
    assert corregir(client, patente="AB-1231").status_code == 400                   # patente de otra máquina
    assert corregir(client, chasis="CHS1").status_code == 400                       # chasis de otra máquina
    assert corregir(client, patente="!!").status_code == 400
    assert corregir(client, anio="1800").status_code == 400
    assert corregir(client, combustible="Agua").status_code == 400
    assert corregir(client, modelo="").status_code == 400
    assert corregir(client, estado="Rota").status_code == 400
    assert log(bd) == []
    assert corregir(client).status_code == 302                                      # mismos datos propios: permitido


def test_cambiar_solo_el_estado_no_es_una_correccion(client, login, bd):
    preparar(client, login)
    entrar(client, login, "sa")
    assert corregir(client, estado="Inactiva").status_code == 302
    assert bd.execute("SELECT Estado_Operativo_Actual AS e FROM maquinaria").fetchone()["e"] == "Inactiva"
    assert log(bd) == []


# ---------------- pantalla de auditoría ----------------
def test_solo_super_admin_y_gerencia_ven_la_auditoria(client, login):
    assert client.get("/auditoria/").status_code == 302                              # sin sesión
    preparar(client, login)
    assert client.get("/auditoria/").status_code == 302                              # Administrativo
    assert 'href="/auditoria/"' not in client.get("/dashboard").data.decode()
    for quien in ("ger", "sa"):
        entrar(client, login, quien)
        assert client.get("/auditoria/").status_code == 200
        assert 'href="/auditoria/"' in client.get("/dashboard").data.decode()


def test_la_pantalla_muestra_quien_cuando_y_que_cambio(app, client, login, bd):
    preparar(client, login)
    cargar(client, app, vale="V1")
    client.post("/combustible/1/editar", data={"maquina": "1", "fecha_carga": ahora_str(app), "numero_vale": "V1",
                                               "litros": "90", "costo_total": "100.000"})
    cargar(client, app, vale="V2", litros="10", costo="5000")
    client.post("/combustible/2/eliminar")
    entrar(client, login, "sa")
    html = client.get("/auditoria/").data.decode()
    assert "Ana Test" in html and "Modificación" in html and "Eliminación" in html and "AB-1230" in html
    assert "Litros:" in html and "100.00" in html and "90.00" in html
    assert re.search(r"N° de vale:</strong>\s*V2", html)                              # eliminación: muestra todos los campos
    assert not re.search(r"N° de vale:</strong>\s*V1", html)                          # modificación: solo lo que cambió


def test_filtros_de_la_auditoria(app, client, login, bd):
    preparar(client, login, n=2)
    cargar(client, app, vale="A", maquina="1")
    cargar(client, app, vale="B", maquina="2")
    client.post("/combustible/1/eliminar")
    client.post("/combustible/2/eliminar")
    mantencion(client, app)
    client.post("/maquinaria/1/mantenciones/1/eliminar")
    entrar(client, login, "sa")
    t = lambda q: client.get(f"/auditoria/{q}").data.decode()
    assert t("?tabla=registro_mantencion").count("Eliminación") == 1 and "Combustible" not in t("?tabla=registro_mantencion").split("<tbody>")[1]
    assert t("?maquina=2").count("badge text-bg-danger") == 1
    assert "No hay registros de auditoría" in t(f"?desde={(hoy(app) + timedelta(days=1)).isoformat()}")
    assert t(f"?desde={hoy(app).isoformat()}&hasta={hoy(app).isoformat()}").count("Eliminación") == 3
    assert "No hay registros de auditoría" in t(f"?hasta={(hoy(app) - timedelta(days=1)).isoformat()}")
    assert t("?tabla=invalida").count("Eliminación") == 3                              # filtro inválido: se ignora


def test_paginacion_y_escape_en_la_auditoria(app, client, login, bd):
    preparar(client, login)
    for i in range(35):
        bd.execute("INSERT INTO auditoria(Tabla_Afectada, ID_Registro, FK_ID_Maquinaria, Accion, FK_ID_Usuario, Fecha_Hora, "
                   "Datos_Anteriores) VALUES ('carga_combustible', ?, 1, 'Eliminación', 1, ?, ?)",
                   (i, ahora().replace(microsecond=0) if False else "2026-01-01 10:00:00", json.dumps({"Dato": f"valor-{i:02d}"})))
    bd.commit()
    mantencion(client, app, detalle="<script>alert(1)</script> " + "texto largo " * 30)
    client.post("/maquinaria/1/mantenciones/1/eliminar")
    entrar(client, login, "sa")
    p1 = client.get("/auditoria/").data.decode()
    assert "página 1 de 2" in p1 and "&lt;script&gt;" in p1 and "<script>alert(1)</script>" not in p1
    assert "ver completo" in p1                                                       # texto largo plegado
    assert "valor-00" in client.get("/auditoria/?pagina=2").data.decode()


def test_la_auditoria_es_de_solo_lectura_desde_la_aplicacion(app):
    reglas = [r for r in app.url_map.iter_rules() if r.endpoint.startswith("auditoria.")]
    assert [(r.endpoint, sorted(r.methods - {"HEAD", "OPTIONS"})) for r in reglas] == [("auditoria.lista", ["GET"])]


def test_una_maquina_con_auditoria_no_se_puede_eliminar(app, client, login, bd):
    preparar(client, login)
    bd.execute("INSERT INTO auditoria(Tabla_Afectada, ID_Registro, FK_ID_Maquinaria, Accion, FK_ID_Usuario, Fecha_Hora, "
               "Datos_Anteriores) VALUES ('carga_combustible', 1, 1, 'Eliminación', 1, '2026-01-01 10:00:00', '{}')")
    bd.commit()
    client.post("/maquinaria/1/eliminar")
    assert bd.execute("SELECT COUNT(*) AS n FROM maquinaria").fetchone()["n"] == 1
    assert "registros de auditoría" in client.get("/maquinaria/1").data.decode()
