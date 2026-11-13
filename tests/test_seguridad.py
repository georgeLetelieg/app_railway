"""Red de seguridad general: se recorren TODAS las rutas de la aplicación (también las que se agreguen
en el futuro), para que ningún módulo nuevo quede abierto por descuido."""
import re

import pytest

from app import create_app
from tests.conftest import ConfigCsrf

SIN_SESION_OK = {"static", "auth.login", "auth.logout", "main.inicio"}


def reglas(app, solo_post=False):
    for regla in app.url_map.iter_rules():
        if regla.endpoint == "static":
            continue
        url = re.sub(r"<[^>]+>", "1", regla.rule)
        for metodo in sorted(regla.methods - {"HEAD", "OPTIONS"}):
            if not solo_post or metodo == "POST":
                yield regla.endpoint, url, metodo


def test_hay_rutas_que_recorrer(app):
    assert len(list(reglas(app))) > 40          # evita que la prueba pase en vacío si cambia la estructura


def test_toda_ruta_exige_iniciar_sesion(app, client):
    for endpoint, url, metodo in reglas(app):
        if endpoint in SIN_SESION_OK:
            continue
        r = client.open(url, method=metodo)
        assert r.status_code == 302 and "/login" in r.location, (endpoint, metodo, r.status_code)


def test_las_rutas_publicas_son_solo_las_esperadas(app, client):
    assert client.get("/login").status_code == 200
    assert client.get("/").status_code == 302 and client.get("/").location.endswith("/dashboard")
    assert client.post("/logout").status_code == 302


def test_todo_post_exige_token_csrf(bd, tmp_path):
    """Con la protección CSRF activa, ningún POST sin token llega a ejecutarse."""
    app = create_app(ConfigCsrf)
    app.config["UPLOAD_FOLDER"] = tmp_path
    client = app.test_client()
    posts = list(reglas(app, solo_post=True))
    assert len(posts) > 20
    for endpoint, url, _ in posts:
        assert client.post(url).status_code == 400, endpoint


def test_ninguna_ruta_de_modificacion_acepta_a_un_rol_de_solo_lectura(app, client, login):
    """Gerencia solo consulta: todo POST (crear, editar, eliminar...) debe rechazarse sin tocar datos."""
    login("ger@x.cl", "Gerente123")
    for endpoint, url, _ in reglas(app, solo_post=True):
        if endpoint in {"auth.login", "auth.logout"}:
            continue
        r = client.post(url, data={})
        assert r.location.endswith("/dashboard"), (endpoint, r.status_code, r.location)


def test_la_auditoria_solo_la_ven_los_roles_autorizados(app, client, login):
    login("ana@x.cl", "Correcta123")            # Administrativo
    assert client.get("/auditoria/").status_code == 302


@pytest.mark.parametrize("ruta", ["/maquinaria/1/ficha.pdf", "/maquinaria/1/foto", "/maquinaria/1/documentos/1/ver",
                                  "/maquinaria/1/documentos/1/descargar", "/combustible/exportar.csv"])
def test_las_descargas_exigen_sesion(client, ruta):
    r = client.get(ruta)
    assert r.status_code == 302 and "/login" in r.location
