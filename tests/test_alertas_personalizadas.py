from datetime import date, timedelta

import pytest

from app.alertas.motor import evaluar
from app.utils import ahora

HOY = date(2026, 11, 8)
MAQ = {"patente": "AB-1234", "modelo": "CAT 320", "anio": "2018", "combustible": "Diésel",
       "chasis": "CHS1", "estado": "Operativa"}


def fila_maquina(estado="Operativa"):
    # Documentos y mantención al día, para aislar el efecto de las personalizadas
    return {"ID_Maquinaria": 1, "Patente": "AB-1234", "Modelo": "CAT", "Estado_Operativo_Actual": estado,
            "Vencimiento_Revision_Tecnica": HOY + timedelta(days=300),
            "Vencimiento_Permiso_Circulacion": HOY + timedelta(days=300),
            "Proxima_Mantencion_Preventiva": 5000, "horometro": 1000}


def pers(dias, id_=1, titulo="Cambio de correas"):
    return {"ID_Alerta_Personalizada": id_, "FK_ID_Maquinaria": 1, "Titulo_Alerta": titulo,
            "Fecha_Vencimiento": HOY + timedelta(days=dias)}


# ---------------- motor ----------------
@pytest.mark.parametrize("dias, esperado", [(-1, "vencida"), (0, "por_vencer"), (7, "por_vencer"),
                                            (8, "vigente"), (90, "vigente")])
def test_umbral_de_alertas_personalizadas(dias, esperado):
    r = evaluar(fila_maquina(), HOY, personalizadas=[pers(dias)])
    item = r["items"][-1]
    assert item["estado"] == esperado and item["personalizada"] and item["nombre"] == "Cambio de correas"
    assert item["alerta_id"] == 1


def test_el_umbral_es_configurable_y_0_notifica_solo_el_dia():
    assert evaluar(fila_maquina(), HOY, personalizadas=[pers(20)], dias_personalizadas=30)["items"][-1]["estado"] == "por_vencer"
    assert evaluar(fila_maquina(), HOY, personalizadas=[pers(1)], dias_personalizadas=0)["items"][-1]["estado"] == "vigente"
    assert evaluar(fila_maquina(), HOY, personalizadas=[pers(0)], dias_personalizadas=0)["items"][-1]["estado"] == "por_vencer"


def test_las_personalizadas_alimentan_el_semaforo():
    assert evaluar(fila_maquina(), HOY)["semaforo"] == "verde"
    assert evaluar(fila_maquina(), HOY, personalizadas=[pers(3)])["semaforo"] == "amarillo"
    assert evaluar(fila_maquina(), HOY, personalizadas=[pers(3), pers(-2, 2, "Otra")])["semaforo"] == "rojo"
    assert evaluar(fila_maquina(), HOY, personalizadas=[pers(60)])["semaforo"] == "verde"


def test_maquina_inactiva_ignora_las_personalizadas():
    r = evaluar(fila_maquina("Inactiva"), HOY, personalizadas=[pers(-5)])
    assert r["semaforo"] is None and r["activa"] is False


# ---------------- aplicación ----------------
def hoy_app(app):
    with app.app_context():
        return ahora().date()


def preparar(client, login, n=1):
    login("ana@x.cl", "Correcta123")
    for i in range(n):
        client.post("/maquinaria/nueva", data={**MAQ, "patente": f"AB-123{i}", "chasis": f"CHS{i}"})


def crear(client, titulo, fecha, maquina=1):
    return client.post(f"/maquinaria/{maquina}/alertas/personalizadas/nueva",
                       data={"titulo": titulo, "fecha": fecha.isoformat() if hasattr(fecha, "isoformat") else fecha})


def alertas(bd):
    return bd.execute("SELECT * FROM alerta_personalizada ORDER BY ID_Alerta_Personalizada").fetchall()


def test_requiere_login_y_permiso(app, client, login, bd):
    assert client.get("/maquinaria/1/alertas/personalizadas/nueva").status_code == 302
    preparar(client, login)
    client.post("/logout")
    login("ger@x.cl", "Gerente123")
    assert client.get("/maquinaria/1/alertas/personalizadas/nueva").status_code == 302   # redirige: solo lectura
    assert crear(client, "Cambio de correas", hoy_app(app) + timedelta(days=3)).status_code == 302
    assert alertas(bd) == []


def test_crear_alerta_y_verla_en_el_perfil(app, client, login, bd):
    preparar(client, login)
    h = hoy_app(app)
    assert client.get("/maquinaria/1/alertas/personalizadas/nueva").status_code == 200
    r = crear(client, "  Renovación   de neumáticos  ", h + timedelta(days=5))
    assert r.status_code == 302 and r.location.endswith("/maquinaria/1#alertas-personalizadas")
    a = alertas(bd)[0]
    assert (a["Titulo_Alerta"], a["Fecha_Vencimiento"], a["Estado_Activa"], a["FK_ID_Usuario"]) == \
           ("Renovación de neumáticos", h + timedelta(days=5), 1, 1)       # espacios normalizados
    html = client.get("/maquinaria/1").data.decode()
    assert "Renovación de neumáticos" in html and "Vence en 5 día(s)" in html and "Creada por Ana Test" in html
    assert "Por vencer" in html


def test_validaciones(app, client, login, bd):
    preparar(client, login)
    h = hoy_app(app)
    malos = [("", h), ("ab", h), ("x" * 256, h), ("Título válido", "no-es-fecha"), ("Título válido", ""),
             ("Título válido", "1999-12-31"), ("Título válido", "2101-01-01")]
    for titulo, fecha in malos:
        assert crear(client, titulo, fecha).status_code == 400, (titulo, fecha)
    assert alertas(bd) == []


def test_rechaza_duplicados_exactos(app, client, login, bd):
    preparar(client, login, n=2)
    h = hoy_app(app)
    assert crear(client, "Cambio de aceite", h + timedelta(days=10)).status_code == 302
    assert crear(client, "Cambio de aceite", h + timedelta(days=10)).status_code == 400      # doble clic / repetida
    assert crear(client, "Cambio de aceite", h + timedelta(days=11)).status_code == 302      # otra fecha: ok
    assert crear(client, "Cambio de aceite", h + timedelta(days=10), maquina=2).status_code == 302   # otra máquina: ok
    assert len(alertas(bd)) == 3


def test_fecha_pasada_queda_vencida(app, client, login):
    preparar(client, login)
    crear(client, "Inspección atrasada", hoy_app(app) - timedelta(days=4))
    html = client.get("/maquinaria/1").data.decode()
    assert "Vencida hace 4 día(s)" in html and "Con vencimientos" in html


def test_editar_alerta(app, client, login, bd):
    preparar(client, login, n=2)
    h = hoy_app(app)
    crear(client, "Cambio de correas", h + timedelta(days=5))
    crear(client, "Otra alerta", h + timedelta(days=9))
    assert client.get("/maquinaria/1/alertas/personalizadas/1/editar").status_code == 200
    r = client.post("/maquinaria/1/alertas/personalizadas/1/editar",
                    data={"titulo": "Cambio de correas y poleas", "fecha": (h + timedelta(days=20)).isoformat()})
    assert r.status_code == 302
    a = alertas(bd)[0]
    assert a["Titulo_Alerta"] == "Cambio de correas y poleas" and a["Fecha_Vencimiento"] == h + timedelta(days=20)
    # guardar sin cambiar nada no cuenta como duplicado de sí misma; chocar con otra sí
    same = {"titulo": "Cambio de correas y poleas", "fecha": (h + timedelta(days=20)).isoformat()}
    assert client.post("/maquinaria/1/alertas/personalizadas/1/editar", data=same).status_code == 302
    choque = {"titulo": "Otra alerta", "fecha": (h + timedelta(days=9)).isoformat()}
    assert client.post("/maquinaria/1/alertas/personalizadas/1/editar", data=choque).status_code == 400
    assert client.post("/maquinaria/1/alertas/personalizadas/1/editar", data={"titulo": "", "fecha": ""}).status_code == 400


def test_una_alerta_solo_se_alcanza_desde_su_maquina(app, client, login):
    preparar(client, login, n=2)
    crear(client, "Cambio de correas", hoy_app(app) + timedelta(days=5))
    for ruta in ("editar",):
        assert client.get(f"/maquinaria/2/alertas/personalizadas/1/{ruta}").status_code == 404
    for accion in ("completar", "reactivar", "eliminar"):
        assert client.post(f"/maquinaria/2/alertas/personalizadas/1/{accion}").status_code == 404
    assert client.get("/maquinaria/99/alertas/personalizadas/nueva").status_code == 404


def test_completar_y_reactivar(app, client, login, bd):
    preparar(client, login)
    crear(client, "Cambio de correas", hoy_app(app) - timedelta(days=2))
    assert "Cambio de correas" in client.get("/alertas/").data.decode()
    assert client.get("/maquinaria/1/alertas/personalizadas/1/completar").status_code == 405
    assert client.post("/maquinaria/1/alertas/personalizadas/1/completar").status_code == 302
    assert alertas(bd)[0]["Estado_Activa"] == 0
    client.get("/dashboard")                                         # consume mensajes flash
    assert "Cambio de correas" not in client.get("/alertas/").data.decode()      # ya no se notifica
    perfil = client.get("/maquinaria/1").data.decode()
    assert "Cambio de correas" in perfil and "Cumplida" in perfil and "Reactivar" in perfil   # queda en el historial
    assert "Con vencimientos" not in perfil
    assert client.post("/maquinaria/1/alertas/personalizadas/1/reactivar").status_code == 302
    assert alertas(bd)[0]["Estado_Activa"] == 1
    assert "Cambio de correas" in client.get("/alertas/").data.decode()


def test_eliminar_alerta(app, client, login, bd):
    preparar(client, login)
    crear(client, "Cambio de correas", hoy_app(app) + timedelta(days=5))
    assert client.get("/maquinaria/1/alertas/personalizadas/1/eliminar").status_code == 405
    assert client.post("/maquinaria/1/alertas/personalizadas/1/eliminar").status_code == 302
    assert alertas(bd) == []
    assert client.post("/maquinaria/1/alertas/personalizadas/1/eliminar").status_code == 404


def test_centro_de_alertas_y_menu_incluyen_las_personalizadas(app, client, login):
    preparar(client, login, n=2)
    h = hoy_app(app)
    crear(client, "Cambio de neumáticos", h - timedelta(days=1), maquina=1)       # vencida
    crear(client, "Revisión de frenos", h + timedelta(days=3), maquina=2)         # por vencer
    crear(client, "Lejana", h + timedelta(days=60), maquina=2)                    # vigente: no aparece
    client.get("/dashboard")
    html = client.get("/alertas/").data.decode()
    assert "Personalizada" in html and "Lejana" not in html
    assert html.index("Cambio de neumáticos") < html.index("Revisión de frenos")   # vencida primero
    assert "/maquinaria/1/alertas/personalizadas/1/editar" in html                 # botón Actualizar apunta a la alerta
    assert 'badge text-bg-danger">2<' in client.get("/dashboard").data.decode()    # contador del menú


def test_los_datos_se_ven_en_el_dashboard_y_la_lista(app, client, login):
    preparar(client, login)
    crear(client, "Cambio de neumáticos", hoy_app(app) + timedelta(days=2))
    assert "Cambio de neumáticos" in client.get("/dashboard").data.decode()
    assert "Por vencer" in client.get("/maquinaria/").data.decode()


def test_el_perfil_sin_alertas_personalizadas(client, login):
    preparar(client, login)
    assert "No hay alertas personalizadas para esta maquinaria." in client.get("/maquinaria/1").data.decode()


def test_maquina_con_alertas_personalizadas_no_se_elimina(app, client, login, bd):
    preparar(client, login)
    crear(client, "Cambio de correas", hoy_app(app) + timedelta(days=5))
    client.post("/maquinaria/1/eliminar")
    assert bd.execute("SELECT COUNT(*) AS n FROM maquinaria").fetchone()["n"] == 1
