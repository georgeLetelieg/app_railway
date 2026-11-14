"""Pruebas de las correcciones de seguridad e integridad (versión 2.4)."""
import re
from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from werkzeug.datastructures import FileStorage
from werkzeug.security import generate_password_hash

from app.utils import ahora

MAQ = {"patente": "AB-1230", "modelo": "CAT 320", "anio": "2018", "combustible": "Diésel",
       "chasis": "CHS0", "estado": "Operativa"}


def hoy(app):
    with app.app_context():
        return ahora().date()


def nueva_maquina(client, login):
    login("ana@x.cl", "Correcta123")
    assert client.post("/maquinaria/nueva", data=MAQ).status_code == 302


def mantencion(client, app, dias_atras, lectura, **extra):
    fecha = hoy(app) - timedelta(days=dias_atras)
    return client.post("/maquinaria/1/mantenciones/nueva",
                       data={"fecha": fecha.isoformat(), "lectura": lectura, "detalle": "Cambio de aceite", **extra})


def alerta(bd):
    return bd.execute("SELECT Proxima_Mantencion_Preventiva AS meta, FK_ID_Mantencion_Origen AS origen, "
                      "Intervalo_Mantencion AS intervalo FROM alerta_estatica WHERE FK_ID_Maquinaria = 1").fetchone()


# --------------------------------------------------------------------------
# 1. XSS en las confirmaciones
# --------------------------------------------------------------------------
def test_confirmacion_no_ejecuta_el_nombre_como_codigo(client, login, bd):
    bd.execute("INSERT INTO usuario(Nombre_Completo, Correo, Contrasena_Hash, FK_ID_Rol) VALUES (?,?,?,1)",
               ("x');alert(document.cookie);//", "xss@x.cl", generate_password_hash("Clave12345", method="pbkdf2:sha256")))
    bd.commit()
    login("sa@x.cl", "Super12345")
    html = client.get("/usuarios/").data.decode()
    assert "onsubmit" not in html and "confirm('" not in html
    assert 'data-confirmar="¿Inactivar la cuenta de x&#39;);alert(document.cookie);//' in html


def test_ninguna_plantilla_usa_manejadores_en_linea(app):
    for nombre in app.jinja_env.list_templates():
        fuente = app.jinja_loader.get_source(app.jinja_env, nombre)[0]
        assert not re.search(r"\son(submit|click|change|input|load)=", fuente), nombre
        assert "<script>" not in fuente, f"{nombre}: todo <script> en línea debe llevar nonce"


# --------------------------------------------------------------------------
# 7. Cabeceras de seguridad
# --------------------------------------------------------------------------
def test_cabeceras_de_seguridad_y_nonce(client, login):
    r = client.get("/login")
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    csp = r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert f'<script nonce="{nonce}">' in r.data.decode()
    assert client.get("/login").headers["Content-Security-Policy"] != csp          # nonce distinto por petición


def test_sri_en_bootstrap(client):
    html = client.get("/login").data.decode()
    assert html.count('integrity="sha384-') == 2 and html.count('crossorigin="anonymous"') == 2


# --------------------------------------------------------------------------
# 4, 5 y 6. Inicio de sesión
# --------------------------------------------------------------------------
def test_no_se_revela_si_la_cuenta_existe(login):
    for correo, clave in (("nadie@x.cl", "Mala12345"), ("off@x.cl", "mala"), ("ana@x.cl", "mala")):
        r = login(correo, clave)
        assert r.status_code == 401 and "Credenciales incorrectas." in r.data.decode()
        assert b"deshabilitada" not in r.data and b"bloqueada" not in r.data


def test_bloqueo_no_se_anuncia_hasta_usar_la_clave_correcta(login, bd):
    for _ in range(3):
        r = login("ana@x.cl", "mala")
        assert r.status_code == 401 and b"bloqueada" not in r.data
    assert bd.execute("SELECT Fecha_Hora_Bloqueo FROM usuario WHERE Correo='ana@x.cl'").fetchone()["Fecha_Hora_Bloqueo"]
    assert login("ana@x.cl", "mala").status_code == 401                         # bloqueada + clave mala: genérico
    assert bd.execute("SELECT Intentos_Fallidos FROM usuario WHERE Correo='ana@x.cl'").fetchone()["Intentos_Fallidos"] == 3
    r = login("ana@x.cl", "Correcta123")
    assert r.status_code == 403 and b"bloqueada" in r.data


def test_intentos_agotados_por_peticiones_simultaneas(login, bd):
    """Si otras peticiones ya consumieron los intentos (sin alcanzar a bloquear), ni la clave correcta pasa."""
    bd.execute("UPDATE usuario SET Intentos_Fallidos = 3 WHERE Correo = 'ana@x.cl'")
    bd.commit()
    assert login("ana@x.cl", "Correcta123").status_code == 401
    fila = bd.execute("SELECT Intentos_Fallidos, Fecha_Hora_Bloqueo FROM usuario WHERE Correo='ana@x.cl'").fetchone()
    assert fila["Intentos_Fallidos"] == 4 and fila["Fecha_Hora_Bloqueo"] is not None


def test_limite_de_intentos_por_ip(app, login, bd):
    limite = app.config["MAX_INTENTOS_IP"]
    for i in range(limite):
        assert login(f"usuario{i}@x.cl", "Clave12345").status_code == 401     # cuentas distintas
    r = login("ana@x.cl", "Correcta123")
    assert r.status_code == 429 and "desde esta conexión".encode() in r.data
    assert bd.execute("SELECT COUNT(*) AS n FROM intento_login").fetchone()["n"] == limite


def test_un_login_correcto_no_cuenta_para_el_limite_por_ip(login, bd):
    assert login("ana@x.cl", "Correcta123").status_code == 302
    assert bd.execute("SELECT COUNT(*) AS n FROM intento_login").fetchone()["n"] == 0


# --------------------------------------------------------------------------
# 8. El rol se relee de la base en cada petición
# --------------------------------------------------------------------------
def test_cambio_de_rol_en_la_base_aplica_de_inmediato(client, login, bd):
    login("ana@x.cl", "Correcta123")                                        # Administrativo: puede escribir
    assert client.get("/combustible/nueva").status_code == 200
    bd.execute("UPDATE usuario SET FK_ID_Rol = 2 WHERE Correo = 'ana@x.cl'")   # pasa a Gerencia (solo lectura)
    bd.commit()
    r = client.get("/combustible/nueva")
    assert r.status_code == 302 and r.location.endswith("/dashboard")
    with client.session_transaction() as s:
        assert s["rol"] == "Gerencia" and s["rol_id"] == 2


# --------------------------------------------------------------------------
# 3. Bombas de descompresión
# --------------------------------------------------------------------------
def _png(ancho, alto, modo="1"):
    salida = BytesIO()
    Image.new(modo, (ancho, alto)).save(salida, "PNG")
    return salida.getvalue()


def test_imagen_de_resolucion_excesiva_se_rechaza_sin_decodificar(app):
    from app.archivos import detectar_tipo
    from app.maquinaria.routes import _procesar_foto
    contenido = _png(7000, 7000)                                  # 49 MP, pesa unos pocos KB
    assert len(contenido) < 5 * 1024 * 1024
    with app.app_context():
        with pytest.raises(ValueError, match="resolución máxima"):
            detectar_tipo(contenido)
        with pytest.raises(ValueError, match="resolución máxima"):
            _procesar_foto(FileStorage(BytesIO(contenido), filename="foto.png"))
        assert len(_procesar_foto(FileStorage(BytesIO(_png(2000, 1500, "RGB")), filename="ok.png"))) > 0


# --------------------------------------------------------------------------
# 13. Próxima mantención preventiva al editar o eliminar su mantención de origen
# --------------------------------------------------------------------------
def test_editar_la_mantencion_de_origen_recalcula_la_meta(app, client, login, bd):
    nueva_maquina(client, login)
    assert mantencion(client, app, 5, "1.000", intervalo="250").status_code == 302
    assert (alerta(bd)["meta"], alerta(bd)["origen"], alerta(bd)["intervalo"]) == (1250, 1, 250)
    datos = {"fecha": (hoy(app) - timedelta(days=5)).isoformat(), "lectura": "1.100", "detalle": "Cambio de aceite"}
    r = client.post("/maquinaria/1/mantenciones/1/editar", data=datos, follow_redirects=True)
    assert alerta(bd)["meta"] == 1350 and "recalculada a las 1.350,00" in r.data.decode()


def test_editar_otra_mantencion_no_toca_la_meta(app, client, login, bd):
    nueva_maquina(client, login)
    mantencion(client, app, 10, "900")                            # sin intervalo
    mantencion(client, app, 5, "1.000", intervalo="250")
    datos = {"fecha": (hoy(app) - timedelta(days=10)).isoformat(), "lectura": "950", "detalle": "Otro"}
    assert client.post("/maquinaria/1/mantenciones/1/editar", data=datos).status_code == 302
    assert alerta(bd)["meta"] == 1250


def test_eliminar_la_mantencion_de_origen_deja_la_meta_sin_definir(app, client, login, bd):
    nueva_maquina(client, login)
    mantencion(client, app, 5, "1.000", intervalo="250")
    r = client.post("/maquinaria/1/mantenciones/1/eliminar", follow_redirects=True)
    assert "quedó sin definir" in r.data.decode()
    assert (alerta(bd)["meta"], alerta(bd)["origen"]) == (None, None)


def test_meta_fijada_a_mano_se_desvincula_de_su_mantencion(app, client, login, bd):
    nueva_maquina(client, login)
    mantencion(client, app, 5, "1.000", intervalo="250")
    assert client.post("/maquinaria/1/alertas/editar",
                       data={"revision": "", "permiso": "", "mantencion": "1.400"}).status_code == 302
    assert (alerta(bd)["meta"], alerta(bd)["origen"]) == (1400, None)
    datos = {"fecha": (hoy(app) - timedelta(days=5)).isoformat(), "lectura": "1.100", "detalle": "Cambio de aceite"}
    client.post("/maquinaria/1/mantenciones/1/editar", data=datos)
    assert alerta(bd)["meta"] == 1400                              # la meta manual se respeta


def test_guardar_vencimientos_sin_cambiar_la_meta_mantiene_el_vinculo(app, client, login, bd):
    nueva_maquina(client, login)
    mantencion(client, app, 5, "1.000", intervalo="250")
    client.post("/maquinaria/1/alertas/editar", data={"revision": "", "permiso": "", "mantencion": "1.250,00"})
    assert alerta(bd)["origen"] == 1
