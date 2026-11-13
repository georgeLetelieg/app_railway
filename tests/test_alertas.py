import re
from datetime import date, timedelta

import pytest

from app.alertas.motor import evaluar
from app.utils import ahora

HOY = date(2026, 11, 8)


def fila(revision=None, permiso=None, meta=None, horometro=None, estado="Operativa"):
    return {"ID_Maquinaria": 1, "Patente": "AB-1234", "Modelo": "CAT", "Estado_Operativo_Actual": estado,
            "Vencimiento_Revision_Tecnica": revision, "Vencimiento_Permiso_Circulacion": permiso,
            "Proxima_Mantencion_Preventiva": meta, "horometro": horometro}


def estados(r):
    return {i["clave"]: i["estado"] for i in r["items"]}


# ---------------- motor (lógica pura) ----------------
@pytest.mark.parametrize("dias, esperado", [(-1, "vencida"), (0, "por_vencer"), (1, "por_vencer"),
                                            (30, "por_vencer"), (31, "vigente"), (400, "vigente")])
def test_umbral_de_30_dias_para_documentos(dias, esperado):
    r = evaluar(fila(revision=HOY + timedelta(days=dias)), HOY)
    assert estados(r)["revision_tecnica"] == esperado
    assert estados(evaluar(fila(permiso=HOY + timedelta(days=dias)), HOY))["permiso_circulacion"] == esperado


@pytest.mark.parametrize("meta, esperado", [(1049.99, "vencida"), (1050, "por_vencer"), (1100, "por_vencer"),
                                            (1100.01, "vigente"), (2000, "vigente")])
def test_umbral_de_50_horas_para_mantencion(meta, esperado):
    assert estados(evaluar(fila(meta=meta, horometro=1050), HOY))["mantencion"] == esperado


def test_mensajes_de_detalle():
    r = evaluar(fila(revision=HOY - timedelta(days=5), permiso=HOY, meta=1100, horometro=1080), HOY)
    d = {i["clave"]: i["detalle"] for i in r["items"]}
    assert d["revision_tecnica"] == "Vencida hace 5 día(s)" and d["permiso_circulacion"] == "Vence hoy"
    assert d["mantencion"].startswith("Faltan 20,00 h")
    assert evaluar(fila(meta=1000, horometro=1012.5), HOY)["items"][2]["detalle"].startswith("Excedida por 12,50 h")


def test_datos_faltantes():
    r = evaluar(fila(), HOY)
    assert set(estados(r).values()) == {"sin_dato"}
    assert estados(evaluar(fila(meta=500), HOY))["mantencion"] == "sin_dato"          # meta sin lecturas de horómetro
    assert estados(evaluar(fila(horometro=500), HOY))["mantencion"] == "sin_dato"     # lectura sin meta


def test_prioridad_del_semaforo():
    ok = dict(revision=HOY + timedelta(days=90), permiso=HOY + timedelta(days=90), meta=2000, horometro=1000)
    assert evaluar(fila(**ok), HOY)["semaforo"] == "verde"
    assert evaluar(fila(**{**ok, "revision": None}), HOY)["semaforo"] == "incompleto"
    assert evaluar(fila(**{**ok, "revision": HOY + timedelta(days=10), "permiso": None}), HOY)["semaforo"] == "amarillo"
    assert evaluar(fila(**{**ok, "permiso": HOY - timedelta(days=1), "revision": HOY + timedelta(days=5)}), HOY)["semaforo"] == "rojo"


def test_maquina_inactiva_no_genera_avisos():
    r = evaluar(fila(revision=HOY - timedelta(days=50), estado="Inactiva"), HOY)
    assert r["semaforo"] is None and r["activa"] is False


def test_umbrales_configurables():
    assert estados(evaluar(fila(revision=HOY + timedelta(days=40)), HOY, dias_aviso=45))["revision_tecnica"] == "por_vencer"
    assert estados(evaluar(fila(meta=1200, horometro=1050), HOY, horas_aviso=200))["mantencion"] == "por_vencer"


# ---------------- aplicación ----------------
MAQ = {"patente": "AB-1234", "modelo": "CAT 320", "anio": "2018", "combustible": "Diésel",
       "chasis": "CHS1", "estado": "Operativa"}


def hoy_app(app):
    with app.app_context():
        return ahora().date()


def preparar(client, login, n=1):
    login("ana@x.cl", "Correcta123")
    for i in range(n):
        client.post("/maquinaria/nueva", data={**MAQ, "patente": f"AB-123{i}", "chasis": f"CHS{i}"})


def fijar(client, id_, revision="", permiso="", mantencion=""):
    return client.post(f"/maquinaria/{id_}/alertas/editar",
                       data={"revision": revision, "permiso": permiso, "mantencion": mantencion})


def lecturas(bd, id_, *valores):
    for i, v in enumerate(valores):
        bd.execute("INSERT INTO bitacora_operativa(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Registro, Horometro, "
                   "Estado_Diario) VALUES (?, 1, ?, ?, 'Operativo')", (id_, f"2026-01-{i + 1:02d}", v))
    bd.commit()


def test_requiere_login_y_permiso(client, login):
    assert client.get("/alertas/").status_code == 302
    assert client.get("/maquinaria/1/alertas/editar").status_code == 302
    preparar(client, login)
    client.post("/logout")
    login("ger@x.cl", "Gerente123")
    assert client.get("/alertas/").status_code == 200                   # solo lectura: puede ver
    assert client.get("/maquinaria/1/alertas/editar").status_code == 302
    assert fijar(client, 1, "2030-01-01").status_code == 302
    assert "2030" not in client.get("/maquinaria/1").data.decode()


def test_editar_y_ver_en_el_perfil(app, client, login, bd):
    preparar(client, login)
    h = hoy_app(app)
    r = fijar(client, 1, (h + timedelta(days=10)).isoformat(), (h - timedelta(days=3)).isoformat(), "1.500")
    assert r.status_code == 302
    f = bd.execute("SELECT * FROM alerta_estatica").fetchone()
    assert f["Vencimiento_Revision_Tecnica"] == h + timedelta(days=10) and f["Proxima_Mantencion_Preventiva"] == 1500
    html = client.get("/maquinaria/1").data.decode()
    assert "Vence en 10 día(s)" in html and "Vencida hace 3 día(s)" in html and "Sin lecturas de horómetro" in html
    assert "Con vencimientos" in html                                     # semáforo rojo en el perfil
    assert client.get("/maquinaria/1/alertas/editar").status_code == 200


def test_editar_permite_vaciar_valores(app, client, login, bd):
    preparar(client, login)
    fijar(client, 1, (hoy_app(app) + timedelta(days=10)).isoformat(), mantencion="900")
    fijar(client, 1)
    f = bd.execute("SELECT * FROM alerta_estatica").fetchone()
    assert (f["Vencimiento_Revision_Tecnica"], f["Proxima_Mantencion_Preventiva"]) == (None, None)


def test_validaciones_del_formulario(client, login, bd):
    preparar(client, login)
    assert fijar(client, 1, revision="no-es-fecha").status_code == 400
    assert fijar(client, 1, permiso="1999-12-31").status_code == 400
    assert fijar(client, 1, permiso="2101-01-01").status_code == 400
    assert fijar(client, 1, mantencion="abc").status_code == 400
    assert fijar(client, 1, mantencion="-10").status_code == 400
    f = bd.execute("SELECT * FROM alerta_estatica").fetchone()
    assert f["Vencimiento_Revision_Tecnica"] is None


def test_crea_la_fila_si_no_existia(app, client, login, bd):
    preparar(client, login)
    bd.execute("DELETE FROM alerta_estatica")
    bd.commit()
    assert fijar(client, 1, (hoy_app(app) + timedelta(days=5)).isoformat()).status_code == 302
    assert bd.execute("SELECT COUNT(*) AS n FROM alerta_estatica").fetchone()["n"] == 1


def test_mantencion_usa_la_ultima_lectura_de_la_bitacora(app, client, login, bd):
    preparar(client, login)
    lecturas(bd, 1, 900, 1000, 1020)
    fijar(client, 1, mantencion="1060")
    html = client.get("/maquinaria/1").data.decode()
    assert "Faltan 40,00 h" in html and "1.020,00 h" in html
    bd.execute("INSERT INTO bitacora_operativa(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Registro, "
               "Horometro, Estado_Diario) VALUES (1, 1, '2026-02-01', 1075, 'Operativo')")
    bd.commit()
    assert "Excedida por 15,00 h" in client.get("/maquinaria/1").data.decode()


def test_centro_de_alertas_ordena_por_urgencia_y_excluye_inactivas(app, client, login, bd):
    preparar(client, login, n=3)
    h = hoy_app(app)
    fijar(client, 1, (h + timedelta(days=20)).isoformat(), (h + timedelta(days=200)).isoformat(), "5000")   # por vencer
    fijar(client, 2, (h - timedelta(days=2)).isoformat(), (h + timedelta(days=200)).isoformat(), "5000")    # vencida
    fijar(client, 3, (h - timedelta(days=9)).isoformat())
    client.post("/maquinaria/3/editar", data={"estado": "Inactiva"})                                          # inactiva
    client.get("/dashboard")                                     # consume los mensajes flash pendientes
    html = client.get("/alertas/").data.decode()
    assert html.index("AB-1231") < html.index("AB-1230")        # la vencida (2) va antes que la por vencer (1)
    assert "AB-1232" not in html                                 # la inactiva no genera avisos
    assert "Vencida hace 2 día(s)" in html and "Vence en 20 día(s)" in html


def test_dashboard_y_menu_muestran_las_alertas(app, client, login):
    preparar(client, login, n=2)
    h = hoy_app(app)
    fijar(client, 1, (h - timedelta(days=1)).isoformat(), (h + timedelta(days=100)).isoformat(), "9000")
    fijar(client, 2, (h + timedelta(days=100)).isoformat(), (h + timedelta(days=100)).isoformat(), "9000")
    html = client.get("/dashboard").data.decode()
    assert "Vencida hace 1 día(s)" in html and "AB-1230" in html
    assert 'badge text-bg-danger">1<' in html                    # contador rojo en el menú
    assert "datos de vencimiento incompletos" in html            # la máquina 2 no tiene lecturas de horómetro


def test_sin_alertas_el_menu_no_muestra_contador(app, client, login):
    preparar(client, login)
    html = client.get("/dashboard").data.decode()
    assert "text-bg-danger\">" not in html.split("</nav>")[0] and "text-bg-warning\">" not in html.split("</nav>")[0]


def test_la_lista_de_flota_muestra_el_semaforo(app, client, login):
    preparar(client, login)
    fijar(client, 1, (hoy_app(app) - timedelta(days=1)).isoformat())
    assert "Con vencimientos" in client.get("/maquinaria/").data.decode()


def test_perfil_de_maquina_inactiva_aclara_que_no_genera_avisos(client, login):
    preparar(client, login)
    client.post("/maquinaria/1/editar", data={"estado": "Inactiva"})
    assert "no se incluye en los avisos generales" in client.get("/maquinaria/1").data.decode()


# ---------------- contadores del centro de alertas (cuentan ALERTAS, no maquinarias) ----------------
def tarjetas(html):
    """Lee las 4 tarjetas del centro de alertas: {'Vencidas': n, 'Por vencer': n, ...}."""
    pares = re.findall(r'<div class="h3 mb-0 text-\w+">(\d+)</div><div class="small text-muted">([^<]+)</div>', html)
    return {etiqueta: int(n) for n, etiqueta in pares}


def filas_de_la_tabla(html):
    cuerpo = html.split("<tbody>")[1].split("</tbody>")[0]
    return cuerpo.count("<tr>") if "Todo al día" not in cuerpo else 0


def centro(client):
    client.get("/dashboard")                      # consume mensajes flash pendientes
    return client.get("/alertas/").data.decode()


def test_reporte_los_contadores_reflejan_cada_alerta_de_forma_independiente(app, client, login):
    """Escenario del reporte: 1 vencida + 1 por vencer + 1 sin registrar en la misma maquinaria."""
    preparar(client, login)
    h = hoy_app(app)
    fijar(client, 1, (h - timedelta(days=5)).isoformat(), (h + timedelta(days=2)).isoformat())   # sin meta de mantención
    html = centro(client)
    assert tarjetas(html) == {"Vencidas": 1, "Por vencer": 1, "Sin registrar": 1, "Vigentes": 0}
    assert filas_de_la_tabla(html) == 3                                                         # 1 + 1 + 1


def test_varias_alertas_por_vencer_se_suman_todas(app, client, login, bd):
    preparar(client, login)
    h = hoy_app(app)
    bd.execute("INSERT INTO bitacora_operativa(FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Registro, Horometro, Estado_Diario) "
               "VALUES (1, 1, ?, 1000, 'Operativo')", (h.isoformat(),))
    bd.commit()
    fijar(client, 1, (h + timedelta(days=10)).isoformat(), (h + timedelta(days=20)).isoformat(), "5000")
    client.post("/maquinaria/1/alertas/personalizadas/nueva", data={"titulo": "Cambio de correas", "fecha": (h + timedelta(days=3)).isoformat()})
    t = tarjetas(centro(client))
    assert t == {"Vencidas": 0, "Por vencer": 3, "Sin registrar": 0, "Vigentes": 1}              # antes habría mostrado 1


def test_los_contadores_suman_las_alertas_de_todas_las_maquinas_y_excluyen_las_inactivas(app, client, login):
    preparar(client, login, n=3)
    h = hoy_app(app)
    fijar(client, 1, (h - timedelta(days=1)).isoformat(), (h - timedelta(days=9)).isoformat())    # 2 vencidas (+1 sin meta)
    fijar(client, 2, (h + timedelta(days=5)).isoformat(), (h + timedelta(days=6)).isoformat())    # 2 por vencer (+1 sin meta)
    fijar(client, 3, (h - timedelta(days=30)).isoformat())
    client.post("/maquinaria/3/editar", data={"estado": "Inactiva"})                              # no cuenta
    t = tarjetas(centro(client))
    assert t == {"Vencidas": 2, "Por vencer": 2, "Sin registrar": 2, "Vigentes": 0}               # la inactiva (3) no suma nada


def test_tarjetas_tabla_y_menu_siempre_coinciden(app, client, login):
    preparar(client, login, n=2)
    h = hoy_app(app)
    fijar(client, 1, (h - timedelta(days=5)).isoformat(), (h + timedelta(days=2)).isoformat())
    fijar(client, 2, (h + timedelta(days=15)).isoformat())
    client.post("/maquinaria/2/alertas/personalizadas/nueva", data={"titulo": "Revisión de frenos", "fecha": (h - timedelta(days=1)).isoformat()})
    html = centro(client)
    t = tarjetas(html)
    assert filas_de_la_tabla(html) == t["Vencidas"] + t["Por vencer"] + t["Sin registrar"]
    menu = re.search(r'Alertas\s*<span class="badge text-bg-\w+">(\d+)</span>', html)
    assert menu and int(menu.group(1)) == t["Vencidas"] + t["Por vencer"]


def test_el_resumen_por_maquinaria_sigue_disponible_y_aclarado(app, client, login):
    preparar(client, login)
    h = hoy_app(app)
    fijar(client, 1, (h - timedelta(days=5)).isoformat(), (h + timedelta(days=2)).isoformat())
    html = centro(client)
    assert "Las cifras cuentan <strong>alertas</strong> (no maquinarias)" in html
    assert "Por maquinaria (1 activa(s)): 1 con vencimientos · 0 por vencer" in html


def test_resumen_sin_alertas(client, login):
    preparar(client, login)
    t = tarjetas(centro(client))
    assert t == {"Vencidas": 0, "Por vencer": 0, "Sin registrar": 3, "Vigentes": 0}                # máquina nueva: 3 datos sin cargar
