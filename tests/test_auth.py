from datetime import timedelta

from app import create_app
from app.utils import ahora
from tests.conftest import ConfigCsrf


def estado(bd, uid=1):
    return bd.execute("SELECT Intentos_Fallidos, Fecha_Hora_Bloqueo FROM usuario WHERE ID_Usuario=?", (uid,)).fetchone()


def test_dashboard_requiere_login(client):
    assert client.get("/dashboard").status_code == 302


def test_correo_inexistente(login):
    assert login("nadie@x.cl", "x").status_code == 401


def test_login_correcto_y_dashboard(client, login):
    r = login("ana@x.cl", "Correcta123")
    assert r.status_code == 302 and r.location.endswith("/dashboard")
    r = client.get("/dashboard")
    assert r.status_code == 200 and b"Ana Test" in r.data and b"Administrativo" in r.data


def test_bloqueo_tras_tres_fallos_aun_con_clave_correcta(app, bd, login):
    with app.app_context():
        for _ in range(3):
            assert login("ana@x.cl", "mala").status_code == 401
        e = estado(bd)
        assert e["Intentos_Fallidos"] == 3 and e["Fecha_Hora_Bloqueo"] is not None
        r = login("ana@x.cl", "Correcta123")
        assert r.status_code == 403 and b"bloqueada" in r.data


def test_bloqueo_expira_a_los_15_minutos(app, bd, login):
    with app.app_context():
        for _ in range(3):
            login("ana@x.cl", "mala")
        bd.execute("UPDATE usuario SET Fecha_Hora_Bloqueo=? WHERE ID_Usuario=1", (ahora() - timedelta(minutes=16),))
        bd.commit()
        assert login("ana@x.cl", "Correcta123").status_code == 302
        e = estado(bd)
        assert e["Intentos_Fallidos"] == 0 and e["Fecha_Hora_Bloqueo"] is None


def test_cuenta_inactiva_no_puede_entrar(login):
    assert login("off@x.cl", "x").status_code == 403


def test_sesion_cae_si_se_desactiva_la_cuenta(client, bd, login):
    login("ana@x.cl", "Correcta123")
    bd.execute("UPDATE usuario SET Estado_Activo=0 WHERE ID_Usuario=1")
    bd.commit()
    assert client.get("/dashboard").status_code == 302


def test_logout_solo_por_post(client, login):
    login("ana@x.cl", "Correcta123")
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 302
    assert client.get("/dashboard").status_code == 302


def test_csrf_activo_rechaza_post_sin_token(bd):
    c = create_app(ConfigCsrf).test_client()
    assert c.post("/login", data={"correo": "a", "password": "b"}).status_code == 400
