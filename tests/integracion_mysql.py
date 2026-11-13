"""Prueba de integración: recorre la aplicación completa contra un MySQL REAL (no la simulación SQLite).

Cómo usarla (sobre una base RECIÉN creada, con el script database/gestion_flota_v2.sql ya ejecutado):

    python tests/integracion_mysql.py

Lee la conexión del archivo .env (DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME). Crea usuarios, máquinas y
registros de prueba, por lo que NO debe ejecutarse sobre una base con datos reales: se niega si ya hay usuarios.
También verifica que los CHECK y los triggers del script estén activos en tu servidor.
"""
import os, sys, json, pathlib
RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("SECRET_KEY", "clave-de-integracion")
from datetime import timedelta
from io import BytesIO
import pymysql
from PIL import Image
from pypdf import PdfReader
from app import create_app
from app.utils import ahora
from config import Config
import tempfile


class Cfg(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SECRET_KEY = "x"
    UPLOAD_FOLDER = pathlib.Path(tempfile.mkdtemp())


app = create_app(Cfg)
fallas, total = [], 0


def ok(cond, msg):
    global total
    total += 1
    if not cond:
        fallas.append(msg)
        print("  X FALLA:", msg)


def sql(q, params=()):
    c = pymysql.connect(host=Cfg.DB_HOST, port=Cfg.DB_PORT, user=Cfg.DB_USER, password=Cfg.DB_PASSWORD,
                        database=Cfg.DB_NAME, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        with c.cursor() as cur:
            cur.execute(q, params)
            r = cur.fetchall()
        c.commit()
        return r
    finally:
        c.close()


def sql_error(q, params=()):
    try:
        sql(q, params)
        return None
    except pymysql.MySQLError as e:
        return e.args


if sql("SELECT COUNT(*) AS n FROM usuario")[0]["n"] > 0:
    sys.exit("La base ya tiene usuarios. Esta prueba solo debe ejecutarse sobre una base recién creada.")
import subprocess
ENTORNO = {**os.environ, "FLASK_DEBUG": "0"}
for nombre, correo, rol, clave in (("Ana Admin", "ana@x.cl", "Administrativo", "Clave12345"),
                                   ("Sara Super", "sa@x.cl", "Super Administrador", "Clave12345"),
                                   ("Gabi Gerente", "ger@x.cl", "Gerencia", "Clave12345")):
    # Comando real de consola, como lo usará el equipo:  flask --app run crear-usuario
    r = subprocess.run(["flask", "--app", "run", "crear-usuario", "--nombre", nombre, "--correo", correo, "--rol", rol,
                        "--password", clave], input=clave + "\n", capture_output=True, text=True, env=ENTORNO,
                       cwd=str(RAIZ))
    ok(r.returncode == 0 and "creado" in r.stdout, f"flask crear-usuario {correo}: {r.stdout} {r.stderr[-300:]}")
r = subprocess.run(["flask", "--app", "run", "crear-usuario", "--nombre", "Rol Inexistente", "--correo", "x@x.cl", "--rol", "NoExiste", "--password", "Clave12345"],
                   input="Clave12345\n", capture_output=True, text=True, env=ENTORNO, cwd=str(RAIZ))
ok(r.returncode != 0 and "no existe" in (r.stdout + r.stderr), "crear-usuario con rol inexistente falla con mensaje claro")


def sesion(correo, clave="Clave12345"):
    c = app.test_client()
    r = c.post("/login", data={"correo": correo, "password": clave})
    ok(r.status_code == 302, f"login {correo}: {r.status_code}")
    return c


ana, sa, ger = sesion("ana@x.cl"), sesion("sa@x.cl"), sesion("ger@x.cl")
with app.app_context():
    hoy = ahora().date()
    now = ahora().strftime("%Y-%m-%dT%H:%M")


def png():
    b = BytesIO()
    Image.new("RGB", (800, 600), "orange").save(b, "PNG")
    b.seek(0)
    return b


print("1. Maquinaria")
r = ana.post("/maquinaria/nueva", data={"patente": "ab-1234", "modelo": "Caterpillar 320 Excavadora", "anio": "2018",
             "combustible": "Diésel", "chasis": "chs-001", "estado": "Operativa", "foto": (png(), "f.png")},
             content_type="multipart/form-data")
ok(r.status_code == 302, f"crear máquina {r.status_code}")
ana.post("/maquinaria/nueva", data={"patente": "CD-5678", "modelo": "Komatsu", "anio": "2020", "combustible": "Diésel",
                                    "chasis": "chs-002", "estado": "Operativa"})
ok(len(sql("SELECT * FROM alerta_estatica")) == 2, "fila alerta_estatica por máquina")
ok(ana.get("/maquinaria/").status_code == 200, "listar")
ok(ana.post("/maquinaria/nueva", data={"patente": "ab-1234", "modelo": "x", "anio": "2018", "combustible": "Diésel",
                                       "chasis": "otro", "estado": "Operativa"}).status_code == 400, "patente duplicada rechazada")
ok(ana.get("/maquinaria/1/foto").status_code == 200, "foto servida")
ok(b"AB-1234" in ana.get("/maquinaria/?q=cater&estado=Operativa").data, "búsqueda")

print("2. Bitácora")
f = lambda d: (hoy - timedelta(days=d)).isoformat()
reg = lambda d, **k: ana.post("/maquinaria/1/bitacora/nuevo", data={"fecha": f(d), "horometro": "", "kilometraje": "",
                                                                    "estado_diario": "Operativo", **k})
ok(reg(3, horometro="1.250,5", kilometraje="30.000").status_code == 302, "registro con formato chileno")
ok(reg(2, estado_diario="Día en taller").status_code == 302, "día sin actividad")
ok(reg(1, horometro="1.280").status_code == 302, "registro 3")
ok(reg(1, horometro="1.290").status_code == 400, "un registro por día")
ok(reg(0, horometro="1.200").status_code == 400, "lectura no puede retroceder")
ok([float(x["Horometro"]) for x in sql("SELECT Horometro FROM bitacora_operativa WHERE Horometro IS NOT NULL ORDER BY Fecha_Registro")] == [1250.5, 1280.0], "valores guardados")
e = sql_error("UPDATE bitacora_operativa SET Horometro = 1 WHERE ID_Bitacora = 1")
ok(e and e[0] == 1644, f"trigger UPDATE bitácora: {e}")
e = sql_error("DELETE FROM bitacora_operativa WHERE ID_Bitacora = 1")
ok(e and e[0] == 1644, f"trigger DELETE bitácora: {e}")
ok(ana.get("/maquinaria/1/bitacora/").status_code == 200, "lista bitácora")

print("3. Documentos")
pdf = b"%PDF-1.4\n" + b"0" * 1000
r = ana.post("/maquinaria/1/documentos/subir", data={"categoria": "1", "archivo": (BytesIO(pdf), "Revisión técnica 2026.pdf")},
             content_type="multipart/form-data")
ok(r.status_code == 302, "subir PDF")
ok(ana.get("/maquinaria/1/documentos/1/ver").data == pdf, "ver PDF")
justo = b"%PDF-" + b"0" * (5 * 1024 * 1024 - 5)
ok(ana.post("/maquinaria/1/documentos/subir", data={"categoria": "2", "archivo": (BytesIO(justo), "grande.pdf")},
            content_type="multipart/form-data").status_code == 302, "PDF de exactamente 5 MB")
ok(str(sql("SELECT Tamano_Archivo_MB t FROM documento ORDER BY ID_Documento")[1]["t"]) == "5.00", "tamaño 5.00 MB")
e = sql_error("INSERT INTO documento (FK_ID_Maquinaria, FK_ID_Usuario, FK_ID_Categoria, Ruta_Archivo, Nombre_Original, Tamano_Archivo_MB, Fecha_Subida) VALUES (1,1,1,'x','x',6.00,NOW())")
ok(e and e[0] == 3819, f"CHECK de 5 MB aplicado por MySQL: {e}")
ok(ana.post("/maquinaria/1/documentos/1/editar", data={"categoria": "3", "nombre": "Permiso"}).status_code == 302, "editar documento")
ok(ana.post("/maquinaria/1/documentos/2/eliminar").status_code == 302, "eliminar documento")

print("4. Combustible + auditoría")
cg = lambda **k: ana.post("/combustible/nueva", data={"maquina": "1", "fecha_carga": now, "numero_vale": "V-1",
                                                      "litros": "120,5", "costo_total": "125.000", **k})
ok(cg().status_code == 302, "registrar carga")
ok(cg(numero_vale="V-2", maquina="2", litros="80", costo_total="85000").status_code == 302, "carga máquina 2")
ok(cg().status_code == 400, "vale duplicado")
r = ana.get("/combustible/")
ok(r.status_code == 200 and "$210.000".encode() in r.data and "200,50 L".encode() in r.data, "cuadratura (GROUP BY con ONLY_FULL_GROUP_BY)")
ok(ana.get("/combustible/exportar.csv").status_code == 200, "CSV")
e = sql_error("INSERT INTO carga_combustible (FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Carga, Litros_Cargados, Costo_Total) VALUES (1,1,NOW(),0,10)")
ok(e and e[0] == 3819, f"CHECK litros>0: {e}")
ok(ana.post("/combustible/1/editar", data={"maquina": "1", "fecha_carga": now, "numero_vale": "V-1", "litros": "100",
                                           "costo_total": "110.000"}).status_code == 302, "editar carga")
ok(ana.post("/combustible/2/eliminar").status_code == 302, "eliminar carga")
a = sql("SELECT * FROM auditoria ORDER BY ID_Auditoria")
ok(len(a) == 2 and [x["Accion"] for x in a] == ["Modificación", "Eliminación"], f"auditoría combustible: {[x['Accion'] for x in a]}")
ok(json.loads(a[0]["Datos_Anteriores"])["Litros"] == "120.50" and json.loads(a[0]["Datos_Nuevos"])["Litros"] == "100.00", "auditoría guarda antes/después")

print("5. Alertas")
ok(ana.post("/maquinaria/1/alertas/editar", data={"revision": (hoy + timedelta(days=10)).isoformat(),
                                                  "permiso": (hoy - timedelta(days=2)).isoformat(), "mantencion": "1.300"}).status_code == 302, "editar vencimientos")
ok(ana.post("/maquinaria/1/alertas/personalizadas/nueva", data={"titulo": "Cambio de neumáticos", "fecha": (hoy + timedelta(days=3)).isoformat()}).status_code == 302, "alerta personalizada")
r = ana.get("/alertas/")
ok(r.status_code == 200 and b"Vencida hace 2" in r.data and "Cambio de neumáticos".encode() in r.data and b"Faltan 20,00 h" in r.data, "centro de alertas (subconsulta de horómetro)")
r = ana.get("/dashboard")
ok(r.status_code == 200 and b"Total de la flota" in r.data, "dashboard")
ok(ana.post("/maquinaria/1/alertas/personalizadas/1/completar").status_code == 302, "completar")

print("6. Mantenciones")
mt = lambda d, l, det, **k: ana.post("/maquinaria/1/mantenciones/nueva", data={"fecha": f(d), "lectura": l, "detalle": det, **k})
ok(mt(5, "1.250,5", "Cambio de aceite\nFiltro hidráulico", intervalo="250").status_code == 302, "registrar mantención + próxima")
ok(float(sql("SELECT Proxima_Mantencion_Preventiva p FROM alerta_estatica WHERE FK_ID_Maquinaria=1")[0]["p"]) == 1500.5, "meta = lectura + intervalo")
largo = ("Diagnóstico extenso del equipo. " * 1900)[:60000]
ok(mt(4, "1.260", largo).status_code == 302, "mantención de 60.000 caracteres")
n_guardado = sql("SELECT CHAR_LENGTH(Detalle_Trabajo) n FROM registro_mantencion ORDER BY ID_Registro")[1]["n"]
ok(n_guardado == len(largo.strip()), f"texto largo íntegro en MySQL (guardado: {n_guardado}, esperado: {len(largo.strip())})")
ok(mt(3, "1.270", "x" * 70000).status_code == 400, "texto que excede TEXT rechazado")
ok(mt(2, "1.100", "retroceso").status_code == 400, "lectura retrocede")
ok(ana.post("/maquinaria/1/mantenciones/2/editar", data={"fecha": f(4), "lectura": "1.265", "detalle": largo[:500]}).status_code == 302, "editar mantención")
ok(ana.post("/maquinaria/1/mantenciones/1/eliminar").status_code == 302, "eliminar mantención")
a = sql("SELECT * FROM auditoria WHERE Tabla_Afectada='registro_mantencion' ORDER BY ID_Auditoria")
ok(len(a) == 2 and len(a[0]["Datos_Anteriores"]) > 60000, "auditoría de texto largo (MEDIUMTEXT)")
ok(ana.get("/maquinaria/1/mantenciones/?q=aceite").status_code == 200, "búsqueda LIKE en TEXT")

print("7. Corrección de datos base (Super Administrador)")
ok(sa.post("/maquinaria/2/editar", data={"patente": "zz-9999", "modelo": "Komatsu PC200", "anio": "2021", "combustible": "Diésel", "chasis": "chs-002", "estado": "Operativa"}).status_code == 302, "SA corrige datos base")
ok(sql("SELECT Patente FROM maquinaria WHERE ID_Maquinaria=2")[0]["Patente"] == "ZZ-9999", "patente corregida")
ok(ana.post("/maquinaria/2/editar", data={"patente": "HACK-1", "modelo": "x", "anio": "1999", "combustible": "Diésel", "chasis": "x", "estado": "Operativa"}).status_code == 302 and sql("SELECT Patente FROM maquinaria WHERE ID_Maquinaria=2")[0]["Patente"] == "ZZ-9999", "Administrativo no puede")
ok(sa.post("/maquinaria/2/editar", data={"patente": "AB-1234", "modelo": "x", "anio": "2020", "combustible": "Diésel", "chasis": "chs-002", "estado": "Operativa"}).status_code == 400, "corrección con patente duplicada")

print("8. Auditoría (pantalla y protección)")
r = sa.get("/auditoria/")
ok(r.status_code == 200 and b"Modificaci" in r.data and b"Eliminaci" in r.data and b"ZZ-9999" in r.data, "pantalla de auditoría")
ok(ger.get("/auditoria/").status_code == 200 and ana.get("/auditoria/").status_code == 302, "permisos de auditoría")
ok(sa.get("/auditoria/?tabla=maquinaria&desde=" + f(1)).status_code == 200, "filtros")
e = sql_error("UPDATE auditoria SET Accion='Eliminación' WHERE ID_Auditoria=1")
ok(e and e[0] == 1644, f"trigger UPDATE auditoría: {e}")
e = sql_error("DELETE FROM auditoria WHERE ID_Auditoria=1")
ok(e and e[0] == 1644, f"trigger DELETE auditoría: {e}")
ok(ana.post("/maquinaria/1/eliminar").status_code == 302 and len(sql("SELECT 1 FROM maquinaria WHERE ID_Maquinaria=1")) == 1, "máquina con historial no se elimina")

print("9. Ficha PDF")
r = ana.get("/maquinaria/1/ficha.pdf?periodo=todo")
ok(r.status_code == 200 and r.data[:4] == b"%PDF", "ficha PDF")
t = "\n".join(p.extract_text() for p in PdfReader(BytesIO(r.data)).pages)
ok("AB-1234" in t and "Diagnóstico extenso" in t and "Vencida hace 2" in t, "contenido de la ficha con datos reales")
print("10. Perfil, formularios y listados con filtros (lecturas SQL de cada pantalla)")
paginas = ["/maquinaria/", "/maquinaria/nueva", "/maquinaria/1", "/maquinaria/2", "/maquinaria/1/editar",
           "/maquinaria/1/bitacora/", "/maquinaria/1/bitacora/nuevo?fecha=" + f(5),
           f"/maquinaria/1/bitacora/?desde={f(10)}&hasta={f(0)}&pagina=1",
           "/maquinaria/1/documentos/", "/maquinaria/1/documentos/?categoria=3", "/maquinaria/1/documentos/subir",
           "/maquinaria/1/documentos/1/editar", "/maquinaria/1/documentos/1/descargar",
           "/combustible/", f"/combustible/?maquina=1&desde={f(30)}&hasta={f(0)}", "/combustible/nueva?maquina=1",
           "/combustible/1/editar", "/combustible/exportar.csv?maquina=1",
           "/maquinaria/1/alertas/editar", "/maquinaria/1/alertas/personalizadas/nueva",
           "/maquinaria/1/alertas/personalizadas/1/editar",
           "/maquinaria/1/mantenciones/", "/maquinaria/1/mantenciones/?q=aceite&pagina=1",
           "/maquinaria/1/mantenciones/nueva", "/maquinaria/1/mantenciones/2/editar",
           "/alertas/", "/dashboard", "/maquinaria/1/ficha.pdf?periodo=30", "/maquinaria/1/ficha.pdf?descargar=1"]
for url in paginas:
    r = ana.get(url)
    ok(r.status_code == 200, f"GET {url}: {r.status_code}")
perfil = ana.get("/maquinaria/1").data.decode()
ok(all(t in perfil for t in ("Alertas de vencimiento", "Alertas personalizadas", "Bitácora operativa", "Documentos",
                             "Combustible", "Mantenciones", "Ficha técnica", "Última carga")), "el perfil muestra todas sus secciones")
ok("Editar vencimientos" in perfil and 'name="periodo"' in perfil, "el perfil ofrece edición y ficha")
ok(b'name="patente"' in sa.get("/maquinaria/1/editar").data and b'name="patente"' not in ana.get("/maquinaria/1/editar").data, "campos base editables solo para Super Administrador")
ok(reg(8, estado_diario="Retroactivo").status_code == 302 and "días sin registro".encode() in ana.get("/maquinaria/1/bitacora/").data
   or "día(s) sin registro".encode() in ana.get("/maquinaria/1/bitacora/").data, "aviso de días sin registro")

print("11. Alertas personalizadas (editar, reactivar, eliminar)")
ok(ana.post("/maquinaria/1/alertas/personalizadas/1/reactivar").status_code == 302, "reactivar")
ok(ana.post("/maquinaria/1/alertas/personalizadas/1/editar", data={"titulo": "Cambio de neumáticos y alineación", "fecha": (hoy + timedelta(days=5)).isoformat()}).status_code == 302, "editar alerta")
ok("alineación".encode() in ana.get("/maquinaria/1").data and "alineación".encode() in ana.get("/alertas/").data, "alerta editada visible")
ok(ana.post("/maquinaria/1/alertas/personalizadas/nueva", data={"titulo": "Cambio de neumáticos y alineación", "fecha": (hoy + timedelta(days=5)).isoformat()}).status_code == 400, "alerta duplicada rechazada")
ok(ana.post("/maquinaria/1/alertas/personalizadas/1/eliminar").status_code == 302 and sql("SELECT COUNT(*) n FROM alerta_personalizada")[0]["n"] == 0, "eliminar alerta")

print("12. Documentos: imágenes y archivos inválidos")
def jpg():
    b = BytesIO()
    Image.new("RGB", (60, 40), "red").save(b, "JPEG")
    b.seek(0)
    return b


mp = dict(content_type="multipart/form-data")
ok(ana.post("/maquinaria/1/documentos/subir", data={"categoria": "4", "archivo": (png(), "scan.png")}, **mp).status_code == 302, "subir PNG como documento")
ok(ana.post("/maquinaria/1/documentos/subir", data={"categoria": "2", "archivo": (jpg(), "factura.jpg")}, **mp).status_code == 302, "subir JPG como documento")
ok(ana.post("/maquinaria/1/documentos/subir", data={"categoria": "2", "archivo": (BytesIO(b"texto disfrazado"), "falso.pdf")}, **mp).status_code == 400, "archivo falso rechazado")
ok(ana.post("/maquinaria/1/documentos/3/editar", data={"categoria": "1", "archivo": (jpg(), "nuevo.jpg")}, **mp).status_code == 302, "reemplazar archivo de un documento")

print("13. Ciclo de vida de una maquinaria sin historial (foto + claves foráneas)")
mq = {"patente": "ef-0003", "modelo": "Temporal", "anio": "2022", "combustible": "Gas", "chasis": "chs-003", "estado": "Operativa"}
ok(ana.post("/maquinaria/nueva", data={**mq, "foto": (png(), "f.png")}, **mp).status_code == 302, "crear máquina 3 con foto")
ok(ana.post("/maquinaria/3/editar", data={"estado": "Inactiva", "foto": (png(), "n.png")}, **mp).status_code == 302, "reemplazar foto")
ok(ana.post("/maquinaria/3/editar", data={"estado": "Operativa", "quitar_foto": "1"}).status_code == 302, "quitar foto")
r = ana.post("/maquinaria/3/eliminar")
ok(r.status_code == 302 and sql("SELECT COUNT(*) n FROM maquinaria WHERE ID_Maquinaria=3")[0]["n"] == 0 and sql("SELECT COUNT(*) n FROM alerta_estatica WHERE FK_ID_Maquinaria=3")[0]["n"] == 0, "eliminar máquina sin historial (respeta las claves foráneas)")

print("14. Rol de solo lectura (Gerencia) no modifica nada")
cuentas = lambda: (sql("SELECT COUNT(*) n FROM carga_combustible")[0]["n"], sql("SELECT COUNT(*) n FROM maquinaria")[0]["n"],
                   sql("SELECT COUNT(*) n FROM registro_mantencion")[0]["n"], sql("SELECT COUNT(*) n FROM bitacora_operativa")[0]["n"])
antes = cuentas()
for url, datos in (("/combustible/nueva", {"maquina": "1", "fecha_carga": now, "litros": "10", "costo_total": "1000"}),
                   ("/combustible/1/eliminar", {}), ("/maquinaria/1/eliminar", {}), ("/maquinaria/2/editar", {"estado": "Inactiva", "patente": "HACK"}),
                   ("/maquinaria/1/mantenciones/nueva", {"fecha": f(1), "lectura": "2000", "detalle": "no debe guardarse"}),
                   ("/maquinaria/1/bitacora/nuevo", {"fecha": f(20), "estado_diario": "x"}), ("/maquinaria/1/alertas/editar", {"mantencion": "1"})):
    r = ger.post(url, data=datos)
    ok(r.status_code == 302 and r.location.endswith("/dashboard"), f"Gerencia rechazada en {url}: {r.status_code}")
ok(cuentas() == antes and sql("SELECT Estado_Operativo_Actual e FROM maquinaria WHERE ID_Maquinaria=2")[0]["e"] == "Operativa", "los datos no cambiaron")

print("15. Autenticación: bloqueo por intentos y cuentas desactivadas (DATETIME real)")
c = app.test_client()
for _ in range(3):
    c.post("/login", data={"correo": "ger@x.cl", "password": "incorrecta"})
u = sql("SELECT Intentos_Fallidos i, Fecha_Hora_Bloqueo b FROM usuario WHERE Correo='ger@x.cl'")[0]
ok(u["i"] == 3 and u["b"] is not None, f"bloqueo guardado: {u}")
r = c.post("/login", data={"correo": "ger@x.cl", "password": "Clave12345"})
ok(r.status_code == 403 and b"bloqueada" in r.data, "cuenta bloqueada rechaza incluso la clave correcta")
with app.app_context():
    vencido = ahora() - timedelta(minutes=16)
sql("UPDATE usuario SET Fecha_Hora_Bloqueo = %s WHERE Correo='ger@x.cl'", (vencido,))
ok(c.post("/login", data={"correo": "ger@x.cl", "password": "Clave12345"}).status_code == 302, "bloqueo vence a los 15 minutos")
u = sql("SELECT Intentos_Fallidos i, Fecha_Hora_Bloqueo b FROM usuario WHERE Correo='ger@x.cl'")[0]
ok(u["i"] == 0 and u["b"] is None, "contador reiniciado tras el login")
ok(app.test_client().post("/login", data={"correo": "inexistente@x.cl", "password": "x"}).status_code == 401, "correo inexistente")
sql("UPDATE usuario SET Estado_Activo = 0 WHERE Correo='ger@x.cl'")
ok(ger.get("/dashboard").status_code == 302, "una sesión abierta cae al desactivar la cuenta")
ok(app.test_client().post("/login", data={"correo": "ger@x.cl", "password": "Clave12345"}).status_code == 403, "cuenta desactivada no puede entrar")

print("16. Gestión de usuarios (solo Super Administrador)")
roles = {r["Nombre_Rol"]: r["ID_Rol"] for r in sql("SELECT ID_Rol, Nombre_Rol FROM rol")}
ok(sa.get("/usuarios/").status_code == 200 and sa.get("/usuarios/nuevo").status_code == 200, "el Super Administrador accede")
for url in ("/usuarios/", "/usuarios/nuevo"):
    r = ana.get(url)
    ok(r.status_code == 302 and r.location.endswith("/dashboard"), f"Administrativo rechazado en {url}: {r.status_code}")
n_usuarios = sql("SELECT COUNT(*) n FROM usuario")[0]["n"]
alta = lambda cli=sa, **k: cli.post("/usuarios/nuevo", data={"nombre": "  Carla   Contadora ", "correo": "CARLA@x.cl", "rol": str(roles["Contador"]),
                                                          "password": "Contable2026", "confirmar": "Contable2026", **k})
ok(alta(ana).status_code == 302 and sql("SELECT COUNT(*) n FROM usuario")[0]["n"] == n_usuarios, "un Administrativo no puede crear usuarios")
ok(alta().status_code == 302, "crear usuario")
u = sql("SELECT * FROM usuario WHERE Correo = 'carla@x.cl'")
ok(len(u) == 1 and u[0]["Nombre_Completo"] == "Carla Contadora" and u[0]["FK_ID_Rol"] == roles["Contador"] and u[0]["Estado_Activo"] == 1, "usuario guardado normalizado")
ok(u[0]["Contrasena_Hash"].startswith("scrypt:") and "Contable2026" not in u[0]["Contrasena_Hash"], "contraseña guardada con hash, nunca en claro")
ok(alta().status_code == 400 and alta(correo="CARLA@X.CL").status_code == 400, "correo duplicado (también con otras mayúsculas)")
ok(alta(correo="otra@x.cl", rol="999").status_code == 400 and alta(correo="otra@x.cl", rol="Contador").status_code == 400, "rol inválido rechazado")
ok(alta(correo="otra@x.cl", password="corta", confirmar="corta").status_code == 400, "contraseña débil rechazada")
e = sql_error("INSERT INTO usuario (Nombre_Completo, Correo, Contrasena_Hash, FK_ID_Rol) VALUES ('x', 'carla@x.cl', 'h', 1)")
ok(e and e[0] == 1062, f"UNIQUE del correo aplicado por MySQL: {e}")
from app.usuarios import queries as _q
_original = _q.existe_correo
_q.existe_correo = lambda cur, correo: False          # simula una carrera: otro admin creó el correo justo antes
r = alta()
_q.existe_correo = _original
ok(r.status_code == 400 and "Ya existe un usuario con ese correo".encode() in r.data and sql("SELECT COUNT(*) n FROM usuario WHERE Correo='carla@x.cl'")[0]["n"] == 1, "carrera por correo duplicado resuelta con un mensaje (IntegrityError real)")
carla_id = u[0]["ID_Usuario"]
carla = app.test_client()
ok(carla.post("/login", data={"correo": "carla@x.cl", "password": "Contable2026"}).status_code == 302, "el usuario nuevo inicia sesión")
ok(carla.get("/usuarios/").status_code == 302 and carla.get("/dashboard").status_code == 200, "un Contador no accede a /usuarios/")
mala = app.test_client()
for _ in range(3):
    mala.post("/login", data={"correo": "carla@x.cl", "password": "incorrecta"})
ok(mala.post("/login", data={"correo": "carla@x.cl", "password": "Contable2026"}).status_code == 403, "cuenta bloqueada por intentos reales")
html = sa.get("/usuarios/").data.decode()
ok("Bloqueado" in html and 'value="desbloquear"' in html, "la lista muestra el bloqueo y el botón Desbloquear")
estado = lambda accion, cli=sa, uid=None: cli.post(f"/usuarios/{uid or carla_id}/estado", data={"accion": accion})
ok(estado("desbloquear").status_code == 302 and mala.post("/login", data={"correo": "carla@x.cl", "password": "Contable2026"}).status_code == 302, "desbloquear permite volver a entrar")
ok(estado("inactivar").status_code == 302 and carla.get("/dashboard").status_code == 302, "inactivar cierra la sesión abierta")
ok(app.test_client().post("/login", data={"correo": "carla@x.cl", "password": "Contable2026"}).status_code == 403, "cuenta inactiva no entra")
ok(estado("activar").status_code == 302 and app.test_client().post("/login", data={"correo": "carla@x.cl", "password": "Contable2026"}).status_code == 302, "activar la deja entrar")
sa_id = sql("SELECT ID_Usuario i FROM usuario WHERE Correo='sa@x.cl'")[0]["i"]
ok(estado("inactivar", uid=sa_id).status_code == 302 and sql("SELECT Estado_Activo e FROM usuario WHERE ID_Usuario=%s", (sa_id,))[0]["e"] == 1, "no puede inactivar su propia cuenta")
ok(estado("borrar").status_code == 400 and estado("inactivar", uid=99999).status_code == 404, "acción inválida y usuario inexistente")
a = sql("SELECT * FROM auditoria WHERE Tabla_Afectada='usuario' ORDER BY ID_Auditoria")
ok(len(a) == 3 and all(x["Accion"] == "Modificación" and x["ID_Registro"] == carla_id for x in a), f"auditoría de cambios de estado: {len(a)} registros")
ok(all("scrypt" not in x["Datos_Anteriores"] + x["Datos_Nuevos"] for x in a), "la auditoría nunca guarda el hash")
ok(b"carla@x.cl" in sa.get("/auditoria/?tabla=usuario").data, "la auditoría indica a qué usuario se le cambió el estado")
r = subprocess.run(["flask", "--app", "run", "crear-usuario", "--nombre", "Débil", "--correo", "debil@x.cl", "--rol", "Contador", "--password", "corta"],
                   input="corta\n", capture_output=True, text=True, env=ENTORNO, cwd=str(RAIZ))
ok(r.returncode != 0 and "al menos 10" in (r.stdout + r.stderr) and not sql("SELECT 1 FROM usuario WHERE Correo='debil@x.cl'"), "el comando de consola aplica la misma política")

ok(ana.post("/logout").status_code == 302, "logout")

print(f"\n{total - len(fallas)}/{total} verificaciones correctas contra MySQL", sql("SELECT VERSION() v")[0]["v"])
if fallas:
    print("Verificaciones fallidas:", *fallas, sep="\n  - ")
sys.exit(1 if fallas else 0)
