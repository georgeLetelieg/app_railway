import json
import re
from datetime import timedelta

import pytest
from werkzeug.security import check_password_hash

from app.cli import crear_usuario
from app.usuarios.validacion import validar_contrasena
from app.utils import ahora

CUENTAS = {"ana": ("ana@x.cl", "Correcta123"), "ger": ("ger@x.cl", "Gerente123"), "sa": ("sa@x.cl", "Super12345")}
# ids sembrados en conftest: 1 ana (Administrativo) · 2 off (inactivo) · 3 ger (Gerencia) · 4 sa (Super Administrador)
# roles: 1 Administrativo · 2 Gerencia · 3 Super Administrador · 4 Contador


def entrar(client, login, quien):
    client.post("/logout")
    login(*CUENTAS[quien])


def sesion_nueva(app, quien):
    c = app.test_client()
    assert c.post("/login", data={"correo": CUENTAS[quien][0], "password": CUENTAS[quien][1]}).status_code == 302
    return c


def usuarios(bd):
    return bd.execute("SELECT * FROM usuario ORDER BY ID_Usuario").fetchall()


def alta(client, **cambios):
    datos = {"nombre": "Carla Contadora", "correo": "carla@x.cl", "rol": "4",
             "password": "Contable2026", "confirmar": "Contable2026", **cambios}
    return client.post("/usuarios/nuevo", data=datos)


def estado(client, usuario_id, accion):
    return client.post(f"/usuarios/{usuario_id}/estado", data={"accion": accion})


def bloquear(app, bd, usuario_id, hace_minutos=5):
    with app.app_context():
        momento = ahora() - timedelta(minutes=hace_minutos)
    bd.execute("UPDATE usuario SET Intentos_Fallidos = 3, Fecha_Hora_Bloqueo = ? WHERE ID_Usuario = ?", (momento, usuario_id))
    bd.commit()


def auditoria(bd):
    return bd.execute("SELECT * FROM auditoria ORDER BY ID_Auditoria").fetchall()


# ---------------- control de acceso ----------------
def test_sin_sesion_se_redirige_al_login(client):
    for ruta in ("/usuarios/", "/usuarios/nuevo"):
        r = client.get(ruta)
        assert r.status_code == 302 and "/login" in r.location
    assert alta(client).status_code == 302 and estado(client, 1, "inactivar").status_code == 302


@pytest.mark.parametrize("quien", ["ana", "ger"])
def test_los_demas_roles_no_tienen_acceso_ni_pueden_modificar(client, login, bd, quien):
    entrar(client, login, quien)
    for ruta in ("/usuarios/", "/usuarios/nuevo"):
        r = client.get(ruta)
        assert r.status_code == 302 and r.location.endswith("/dashboard"), ruta
    assert alta(client).status_code == 302
    assert estado(client, 1, "inactivar").status_code == 302
    assert len(usuarios(bd)) == 4 and usuarios(bd)[0]["Estado_Activo"] == 1 and auditoria(bd) == []


def test_el_super_administrador_si_entra(client, login):
    entrar(client, login, "sa")
    assert client.get("/usuarios/").status_code == 200 and client.get("/usuarios/nuevo").status_code == 200


def test_el_enlace_del_menu_solo_lo_ve_el_super_administrador(client, login):
    for quien, ve in (("ana", False), ("ger", False), ("sa", True)):
        entrar(client, login, quien)
        assert ('href="/usuarios/"' in client.get("/dashboard").data.decode()) is ve, quien


def test_admin_required_revalida_contra_la_base_de_datos(client, login, bd):
    """Aunque la sesión diga «Super Administrador», manda lo que hay en la base de datos."""
    entrar(client, login, "sa")
    assert client.get("/usuarios/").status_code == 200
    bd.execute("UPDATE usuario SET FK_ID_Rol = 2 WHERE ID_Usuario = 4")          # lo bajan a Gerencia
    bd.commit()
    r = client.get("/usuarios/")
    assert r.status_code == 302 and r.location.endswith("/dashboard")
    assert alta(client).status_code == 302 and len(usuarios(bd)) == 4


def test_un_super_administrador_desactivado_pierde_el_acceso_y_la_sesion(client, login, bd):
    entrar(client, login, "sa")
    bd.execute("UPDATE usuario SET Estado_Activo = 0 WHERE ID_Usuario = 4")
    bd.commit()
    r = client.get("/usuarios/")
    assert r.status_code == 302 and "/login" in r.location
    assert client.get("/dashboard").status_code == 302                               # la sesión se cerró


# ---------------- listado ----------------
def test_la_lista_muestra_nombre_correo_rol_y_estado(app, client, login, bd):
    bloquear(app, bd, 3, hace_minutos=5)                                             # ger: bloqueo vigente
    entrar(client, login, "sa")
    html = client.get("/usuarios/").data.decode()
    for texto in ("Ana Test", "ana@x.cl", "Administrativo", "Gerente Test", "Gerencia", "Super Admin", "Super Administrador"):
        assert texto in html, texto
    assert html.count("badge text-bg-secondary\">Inactivo") == 1 and html.count("badge text-bg-danger\">Bloqueado") == 1
    assert html.count("badge text-bg-success\">Activo") == 2                         # ana y sa
    assert "hasta las" in html
    assert "table-responsive" in html


def test_un_bloqueo_vencido_ya_no_cuenta_como_bloqueado(app, client, login, bd):
    bloquear(app, bd, 3, hace_minutos=20)                                            # expiró hace 5 minutos
    entrar(client, login, "sa")
    html = client.get("/usuarios/").data.decode()
    assert "Bloqueado" not in html.split("<tbody>")[1].split("</tbody>")[0] and html.count("badge text-bg-success\">Activo") == 3


def test_el_boton_desbloquear_solo_aparece_en_cuentas_bloqueadas(app, client, login, bd):
    bloquear(app, bd, 3)
    entrar(client, login, "sa")
    html = client.get("/usuarios/").data.decode()
    assert html.count('value="desbloquear"') == 1
    assert re.search(r'/usuarios/3/estado.*?value="desbloquear"', html, re.S)


def test_la_propia_cuenta_no_ofrece_acciones(client, login):
    entrar(client, login, "sa")
    html = client.get("/usuarios/").data.decode()
    assert "(tú)" in html and "/usuarios/4/estado" not in html and "/usuarios/1/estado" in html
    assert 'value="activar"' in html                                                 # la cuenta inactiva (2) ofrece activar


def test_resumen_busqueda_y_filtro_por_estado(app, client, login, bd):
    bloquear(app, bd, 3)
    entrar(client, login, "sa")
    html = client.get("/usuarios/").data.decode()
    assert re.search(r">4</div><div class=\"small text-muted\">Total", html)
    assert re.search(r">2</div><div class=\"small text-muted\">Activos", html)
    assert re.search(r">1</div><div class=\"small text-muted\">Inactivos", html)
    assert re.search(r">1</div><div class=\"small text-muted\">Bloqueados", html)
    por_nombre = client.get("/usuarios/?q=gerente").data.decode().split("<tbody>")[1]
    assert "Gerente Test" in por_nombre and "Ana Test" not in por_nombre
    por_correo = client.get("/usuarios/?q=SA@X").data.decode().split("<tbody>")[1]
    assert "sa@x.cl" in por_correo and "ana@x.cl" not in por_correo
    inactivos = client.get("/usuarios/?estado=inactivo").data.decode().split("<tbody>")[1]
    assert "off@x.cl" in inactivos and "ana@x.cl" not in inactivos
    assert "No hay usuarios que coincidan" in client.get("/usuarios/?q=zzz").data.decode()
    assert "ana@x.cl" in client.get("/usuarios/?estado=invalido").data.decode()      # filtro inválido: se ignora


# ---------------- formulario y creación ----------------
def test_el_rol_es_un_desplegable_con_los_roles_de_la_base(client, login):
    entrar(client, login, "sa")
    html = client.get("/usuarios/nuevo").data.decode()
    assert '<select id="rol" name="rol"' in html and "required" in html.split('<select id="rol"')[1].split(">")[0]
    opciones = re.findall(r'<option value="(\d*)"[^>]*>([^<]*)</option>', html.split('id="rol"')[1].split("</select>")[0])
    assert opciones == [("", "Selecciona un rol…"), ("1", "Administrativo"), ("2", "Gerencia"),
                        ("3", "Super Administrador"), ("4", "Contador")]
    assert 'name="rol" type="text"' not in html and 'name="password"' in html and 'autocomplete="new-password"' in html
    assert "Mínimo 10 caracteres" in html


def test_crear_usuario_encripta_la_contrasena_antes_del_insert(app, client, login, bd):
    entrar(client, login, "sa")
    r = alta(client, nombre="  Carla   Contadora ", correo="  CARLA@X.CL ")
    assert r.status_code == 302 and r.location.endswith("/usuarios/")
    u = usuarios(bd)[-1]
    assert (u["Nombre_Completo"], u["Correo"], u["FK_ID_Rol"], u["Estado_Activo"], u["Intentos_Fallidos"]) == \
           ("Carla Contadora", "carla@x.cl", 4, 1, 0)
    assert u["Contrasena_Hash"] != "Contable2026" and "Contable2026" not in u["Contrasena_Hash"]
    assert u["Contrasena_Hash"].startswith("scrypt:") and check_password_hash(u["Contrasena_Hash"], "Contable2026")
    assert "Carla Contadora creado con el rol Contador" in client.get("/usuarios/").data.decode()


def test_el_metodo_de_hash_sale_de_la_configuracion(app, client, login, bd):
    app.config["METODO_HASH"] = "pbkdf2:sha256"
    entrar(client, login, "sa")
    alta(client)
    assert usuarios(bd)[-1]["Contrasena_Hash"].startswith("pbkdf2:sha256")


def test_el_usuario_nuevo_puede_iniciar_sesion_con_su_rol(app, client, login, bd):
    entrar(client, login, "sa")
    alta(client, correo="carla@x.cl", rol="1")                                       # Administrativo
    nuevo = app.test_client()
    assert nuevo.post("/login", data={"correo": "carla@x.cl", "password": "Contable2026"}).status_code == 302
    assert nuevo.get("/dashboard").status_code == 200 and nuevo.get("/usuarios/").status_code == 302   # no es admin


def test_un_super_administrador_puede_crear_otro(app, client, login):
    entrar(client, login, "sa")
    alta(client, correo="otro@x.cl", rol="3")
    c = app.test_client()
    c.post("/login", data={"correo": "otro@x.cl", "password": "Contable2026"})
    assert c.get("/usuarios/").status_code == 200


@pytest.mark.parametrize("cambio", [
    {"nombre": ""}, {"nombre": "ab"}, {"nombre": "x" * 151},
    {"correo": ""}, {"correo": "sin-arroba"}, {"correo": "a@b"}, {"correo": "con espacio@x.cl"},
    {"correo": "ana@x.cl"}, {"correo": "ANA@X.CL"},                                  # duplicado (sin distinguir mayúsculas)
    {"rol": ""}, {"rol": "99"}, {"rol": "Contador"}, {"rol": "-1"}, {"rol": "1; DROP TABLE usuario"},
    {"password": "", "confirmar": ""}, {"password": "Corta1", "confirmar": "Corta1"},
    {"password": "soloLetrasAqui", "confirmar": "soloLetrasAqui"}, {"password": "1234567890123", "confirmar": "1234567890123"},
    {"password": "carla@x.cl", "confirmar": "carla@x.cl"}, {"password": "Carla Contadora", "confirmar": "Carla Contadora"},
    {"confirmar": "OtraDistinta123"}, {"password": "a1" * 65, "confirmar": "a1" * 65},
])
def test_validaciones_del_formulario(client, login, bd, cambio):
    entrar(client, login, "sa")
    assert alta(client, **cambio).status_code == 400
    assert len(usuarios(bd)) == 4


def test_los_errores_no_devuelven_la_contrasena_en_el_html(client, login):
    entrar(client, login, "sa")
    r = alta(client, correo="sin-arroba", password="Secreta12345", confirmar="Secreta12345")
    html = r.data.decode()
    assert r.status_code == 400 and "Secreta12345" not in html and "Ingresa un correo electrónico válido" in html
    assert 'value="Carla Contadora"' in html and 'value="sin-arroba"' in html           # lo demás sí se conserva


def test_se_muestran_todos_los_errores_de_la_contrasena(client, login):
    entrar(client, login, "sa")
    html = alta(client, password="abc", confirmar="abd").data.decode()
    assert "al menos 10 caracteres" in html and "una letra y un número" in html and "no coinciden" in html


# ---------------- política de contraseñas (unitaria) ----------------
@pytest.mark.parametrize("password, valida", [
    ("Contable2026", True), ("abcdefghi1", True), ("12345678a9", True), ("con espacios 12 ok", True),
    ("corta1A", False), ("sololetrasaqui", False), ("12345678901", False), ("", False), ("a1" * 65, False),
])
def test_politica_de_contrasenas(password, valida):
    assert (validar_contrasena(password, "u@x.cl", "Nombre") == []) is valida


def test_la_contrasena_no_puede_ser_el_correo_ni_su_parte_local_ni_el_nombre():
    assert validar_contrasena("carla2026x", "carla2026x@x.cl", "Carla")
    assert validar_contrasena("Carla@x.cl1", "carla@x.cl1", "Carla")
    assert validar_contrasena("Carla Perez1", "c@x.cl", "Carla Perez1")


# ---------------- cambio de estado ----------------
def test_inactivar_corta_la_sesion_abierta_del_usuario(app, client, login, bd):
    ger = sesion_nueva(app, "ger")
    assert ger.get("/dashboard").status_code == 200
    entrar(client, login, "sa")
    r = estado(client, 3, "inactivar")
    assert r.status_code == 302 and usuarios(bd)[2]["Estado_Activo"] == 0
    assert ger.get("/dashboard").status_code == 302                                   # su sesión cayó
    assert app.test_client().post("/login", data={"correo": "ger@x.cl", "password": "Gerente123"}).status_code == 403
    assert "Cuenta de Gerente Test inactivada" in client.get("/usuarios/").data.decode()


def test_activar_reactiva_y_limpia_el_bloqueo(app, client, login, bd):
    bloquear(app, bd, 3)
    bd.execute("UPDATE usuario SET Estado_Activo = 0 WHERE ID_Usuario = 3")
    bd.commit()
    entrar(client, login, "sa")
    assert estado(client, 3, "activar").status_code == 302
    u = usuarios(bd)[2]
    assert (u["Estado_Activo"], u["Intentos_Fallidos"], u["Fecha_Hora_Bloqueo"]) == (1, 0, None)
    assert app.test_client().post("/login", data={"correo": "ger@x.cl", "password": "Gerente123"}).status_code == 302


def test_desbloquear_una_cuenta_bloqueada_por_intentos_reales(app, client, login, bd):
    c = app.test_client()
    for _ in range(3):
        c.post("/login", data={"correo": "ger@x.cl", "password": "incorrecta"})
    assert c.post("/login", data={"correo": "ger@x.cl", "password": "Gerente123"}).status_code == 403   # bloqueada
    entrar(client, login, "sa")
    assert "Bloqueado" in client.get("/usuarios/").data.decode()
    assert estado(client, 3, "desbloquear").status_code == 302
    u = usuarios(bd)[2]
    assert (u["Intentos_Fallidos"], u["Fecha_Hora_Bloqueo"]) == (0, None)
    assert c.post("/login", data={"correo": "ger@x.cl", "password": "Gerente123"}).status_code == 302   # ya puede entrar


def test_no_se_puede_inactivar_la_propia_cuenta(client, login, bd):
    entrar(client, login, "sa")
    r = estado(client, 4, "inactivar")
    assert r.status_code == 302 and usuarios(bd)[3]["Estado_Activo"] == 1 and auditoria(bd) == []
    assert "No puedes inactivar tu propia cuenta" in client.get("/usuarios/").data.decode()
    assert client.get("/usuarios/").status_code == 200                                # sigue con acceso


def test_acciones_invalidas_y_casos_sin_efecto(client, login, bd):
    entrar(client, login, "sa")
    assert estado(client, 1, "borrar").status_code == 400 and estado(client, 1, "").status_code == 400
    assert client.post("/usuarios/1/estado", data={}).status_code == 400
    assert estado(client, 99, "inactivar").status_code == 404
    assert client.get("/usuarios/1/estado").status_code == 405
    assert estado(client, 1, "activar").status_code == 302                            # ya estaba activa
    assert estado(client, 1, "desbloquear").status_code == 302                        # no estaba bloqueada
    assert estado(client, 2, "inactivar").status_code == 302                          # ya estaba inactiva
    assert auditoria(bd) == []
    assert "No había nada que cambiar" in client.get("/usuarios/").data.decode()


# ---------------- auditoría de los cambios de estado ----------------
def test_los_cambios_de_estado_quedan_en_la_auditoria(app, client, login, bd):
    bloquear(app, bd, 3)
    entrar(client, login, "sa")
    estado(client, 3, "desbloquear")
    estado(client, 3, "inactivar")
    estado(client, 3, "activar")
    a = auditoria(bd)
    assert [(x["Tabla_Afectada"], x["ID_Registro"], x["Accion"], x["FK_ID_Usuario"], x["FK_ID_Maquinaria"]) for x in a] == \
           [("usuario", 3, "Modificación", 4, None)] * 3
    d = [(json.loads(x["Datos_Anteriores"]), json.loads(x["Datos_Nuevos"])) for x in a]
    assert d[0][0] == {"Usuario": "ger@x.cl", "Estado": "Bloqueado", "Intentos fallidos": "3"}
    assert d[0][1] == {"Usuario": "ger@x.cl", "Estado": "Activo", "Intentos fallidos": "0"}
    assert d[1][1]["Estado"] == "Inactivo" and d[2][0]["Estado"] == "Inactivo" and d[2][1]["Estado"] == "Activo"
    assert all("hash" not in x["Datos_Anteriores"].lower() and "scrypt" not in x["Datos_Anteriores"] for x in a)


def test_la_pantalla_de_auditoria_muestra_los_cambios_de_usuarios(client, login):
    entrar(client, login, "sa")
    estado(client, 1, "inactivar")
    html = client.get("/auditoria/?tabla=usuario").data.decode()
    fila = html.split("<tbody>")[1].split("</tbody>")[0]
    assert "Usuarios (cambios de estado)" in fila and "Super Admin" in fila              # el dato y quién lo hizo
    assert '<div class="small text-muted">ana@x.cl</div>' in fila                       # a QUIÉN se le cambió el estado
    assert re.search(r"Estado:</strong>\s*Activo\s*<span class=\"text-muted\">→</span>\s*Inactivo", html)


# ---------------- comando de consola ----------------
def test_el_comando_de_consola_aplica_la_misma_politica(app, bd):
    runner = app.test_cli_runner()
    base = ["--nombre", "Nuevo Usuario", "--correo", "nuevo@x.cl", "--rol", "Administrativo"]
    with app.app_context():
        malo = runner.invoke(crear_usuario, base + ["--password", "corta"], input="corta\n")
        assert malo.exit_code != 0 and "al menos 10 caracteres" in malo.output and len(usuarios(bd)) == 4
        sin_rol = runner.invoke(crear_usuario, ["--nombre", "N N", "--correo", "n@x.cl", "--rol", "NoExiste", "--password", "Clave12345"], input="Clave12345\n")
        assert sin_rol.exit_code != 0 and "no existe" in sin_rol.output
        ok = runner.invoke(crear_usuario, base + ["--password", "Clave12345"], input="Clave12345\n")
        assert ok.exit_code == 0 and "creado" in ok.output
        repetido = runner.invoke(crear_usuario, base + ["--password", "Clave12345"], input="Clave12345\n")
        assert repetido.exit_code != 0 and "Ya existe un usuario con ese correo" in repetido.output
    u = usuarios(bd)[-1]
    assert u["Correo"] == "nuevo@x.cl" and check_password_hash(u["Contrasena_Hash"], "Clave12345") and len(usuarios(bd)) == 5
