"""Pruebas sin MySQL real: se reemplaza la conexión por SQLite en memoria."""
import sqlite3
from datetime import date, datetime
from decimal import Decimal

import pytest
from werkzeug.security import generate_password_hash

from app import create_app, db
from config import Config

sqlite3.register_converter("DATETIME", lambda b: datetime.fromisoformat(b.decode()))
sqlite3.register_adapter(datetime, lambda d: d.isoformat(sep=" "))
sqlite3.register_converter("DATE", lambda b: date.fromisoformat(b.decode()))
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_adapter(Decimal, str)


class ConfigPrueba(Config):
    SECRET_KEY = "clave-de-prueba"
    TESTING = True
    WTF_CSRF_ENABLED = False


class ConfigCsrf(ConfigPrueba):
    WTF_CSRF_ENABLED = True


class _Cursor:
    def __init__(self, conexion): self.c = conexion.cursor()
    def execute(self, sql, params=()): self.c.execute(sql.replace("%s", "?"), tuple(params))
    def fetchone(self): return self.c.fetchone()
    def fetchall(self): return self.c.fetchall()
    @property
    def lastrowid(self): return self.c.lastrowid
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _Conexion:
    def __init__(self, bd): self.bd = bd
    def cursor(self): return _Cursor(self.bd)
    def commit(self): self.bd.commit()
    def rollback(self): self.bd.rollback()
    def close(self): self.bd.rollback()     # como MySQL: lo no confirmado (commit) se descarta al cerrar


@pytest.fixture
def bd(monkeypatch):
    bd = sqlite3.connect(":memory:", detect_types=sqlite3.PARSE_DECLTYPES, check_same_thread=False)
    bd.row_factory = lambda c, r: {d[0]: v for d, v in zip(c.description, r)}
    bd.executescript("""
        CREATE TABLE rol(ID_Rol INTEGER PRIMARY KEY, Nombre_Rol TEXT);
        CREATE TABLE usuario(ID_Usuario INTEGER PRIMARY KEY, Nombre_Completo TEXT, Correo TEXT,
            Contrasena_Hash TEXT, Estado_Activo INT DEFAULT 1, Intentos_Fallidos INT DEFAULT 0,
            Fecha_Hora_Bloqueo DATETIME, FK_ID_Rol INT);
        INSERT INTO rol VALUES (1, 'Administrativo'), (2, 'Gerencia'), (3, 'Super Administrador'), (4, 'Contador');
        CREATE TABLE maquinaria(ID_Maquinaria INTEGER PRIMARY KEY AUTOINCREMENT, Patente TEXT UNIQUE NOT NULL,
            Modelo TEXT NOT NULL, Anio INT NOT NULL, Tipo_Combustible TEXT NOT NULL,
            Numero_Chasis TEXT UNIQUE NOT NULL, Fotografia_Principal TEXT,
            Estado_Operativo_Actual TEXT NOT NULL DEFAULT 'Operativa');
        CREATE TABLE intento_login(ID_Intento INTEGER PRIMARY KEY AUTOINCREMENT, IP TEXT NOT NULL,
            Fecha_Hora DATETIME NOT NULL);
        CREATE TABLE alerta_estatica(ID_Alerta_Estatica INTEGER PRIMARY KEY AUTOINCREMENT, FK_ID_Maquinaria INT UNIQUE NOT NULL,
            Vencimiento_Revision_Tecnica DATE, Vencimiento_Permiso_Circulacion DATE, Proxima_Mantencion_Preventiva DECIMAL(10,2),
            FK_ID_Mantencion_Origen INT, Intervalo_Mantencion DECIMAL(10,2));
        CREATE TABLE bitacora_operativa(ID_Bitacora INTEGER PRIMARY KEY AUTOINCREMENT, FK_ID_Maquinaria INT NOT NULL,
            FK_ID_Usuario INT NOT NULL, Fecha_Registro DATE NOT NULL, Fecha_Hora_Ingreso DATETIME,
            Horometro DECIMAL(10,2), Kilometraje DECIMAL(10,2), Estado_Diario TEXT NOT NULL,
            Numero_Parte TEXT UNIQUE, Faena TEXT, Operador TEXT, Horometro_Inicio DECIMAL(10,2),
            Horas_Trabajadas DECIMAL(10,2) GENERATED ALWAYS AS (Horometro - Horometro_Inicio) STORED,
            Turno TEXT, Hora_Comienzo TEXT, Hora_Termino TEXT, Tipo_Trabajo TEXT, Observaciones TEXT,
            Combustible_Litros DECIMAL(10,2),
            UNIQUE (FK_ID_Maquinaria, Fecha_Registro));
        CREATE TABLE categoria_documento(ID_Categoria INTEGER PRIMARY KEY AUTOINCREMENT, Nombre_Categoria TEXT UNIQUE NOT NULL);
        INSERT INTO categoria_documento(Nombre_Categoria) VALUES ('Revisión técnica'), ('Factura de compra'),
            ('Permiso de circulación'), ('Mantención correctiva');
        CREATE TABLE documento(ID_Documento INTEGER PRIMARY KEY AUTOINCREMENT, FK_ID_Maquinaria INT NOT NULL,
            FK_ID_Usuario INT NOT NULL, FK_ID_Categoria INT NOT NULL, Ruta_Archivo TEXT NOT NULL,
            Nombre_Original TEXT NOT NULL, Tamano_Archivo_MB DECIMAL(5,2) NOT NULL
                CHECK (Tamano_Archivo_MB > 0 AND Tamano_Archivo_MB <= 5),
            Fecha_Subida DATETIME NOT NULL);
        CREATE TABLE carga_combustible(ID_Carga INTEGER PRIMARY KEY AUTOINCREMENT, FK_ID_Maquinaria INT NOT NULL,
            FK_ID_Usuario INT NOT NULL, Fecha_Carga DATETIME NOT NULL, Numero_Vale TEXT,
            Litros_Cargados DECIMAL(10,2) NOT NULL CHECK (Litros_Cargados > 0),
            Costo_Total DECIMAL(12,2) NOT NULL CHECK (Costo_Total >= 0),
            UNIQUE (FK_ID_Maquinaria, Numero_Vale));
        CREATE TABLE alerta_personalizada(ID_Alerta_Personalizada INTEGER PRIMARY KEY AUTOINCREMENT, FK_ID_Maquinaria INT NOT NULL,
            FK_ID_Usuario INT NOT NULL, Titulo_Alerta TEXT NOT NULL, Fecha_Vencimiento DATE NOT NULL,
            Estado_Activa INT NOT NULL DEFAULT 1, UNIQUE (FK_ID_Maquinaria, Titulo_Alerta, Fecha_Vencimiento));
        CREATE TABLE auditoria(ID_Auditoria INTEGER PRIMARY KEY AUTOINCREMENT, Tabla_Afectada TEXT NOT NULL,
            ID_Registro INT NOT NULL, FK_ID_Maquinaria INT, Accion TEXT NOT NULL, FK_ID_Usuario INT NOT NULL,
            Fecha_Hora DATETIME NOT NULL, Datos_Anteriores TEXT NOT NULL, Datos_Nuevos TEXT);
        CREATE TABLE registro_mantencion(ID_Registro INTEGER PRIMARY KEY AUTOINCREMENT, FK_ID_Maquinaria INT NOT NULL,
            FK_ID_Usuario INT NOT NULL, Fecha_Mantencion DATE NOT NULL,
            Horometro_Mecanico DECIMAL(10,2) NOT NULL CHECK (Horometro_Mecanico >= 0), Detalle_Trabajo TEXT NOT NULL);
    """)
    bd.execute("INSERT INTO usuario(Nombre_Completo, Correo, Contrasena_Hash, FK_ID_Rol) VALUES (?,?,?,1)",
               ("Ana Test", "ana@x.cl", generate_password_hash("Correcta123", method="scrypt")))
    bd.execute("INSERT INTO usuario(Nombre_Completo, Correo, Contrasena_Hash, Estado_Activo, FK_ID_Rol) VALUES (?,?,?,0,1)",
               ("Inactivo", "off@x.cl", generate_password_hash("x", method="pbkdf2:sha256")))
    bd.execute("INSERT INTO usuario(Nombre_Completo, Correo, Contrasena_Hash, FK_ID_Rol) VALUES (?,?,?,2)",
               ("Gerente Test", "ger@x.cl", generate_password_hash("Gerente123", method="scrypt")))
    bd.execute("INSERT INTO usuario(Nombre_Completo, Correo, Contrasena_Hash, FK_ID_Rol) VALUES (?,?,?,3)",
               ("Super Admin", "sa@x.cl", generate_password_hash("Super12345", method="scrypt")))
    bd.commit()
    monkeypatch.setattr(db, "get_db_connection", lambda: _Conexion(bd))
    return bd


@pytest.fixture
def app(bd, tmp_path):
    app = create_app(ConfigPrueba)
    app.config["UPLOAD_FOLDER"] = tmp_path / "uploads"
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def login(client):
    def _login(correo, password):
        return client.post("/login", data={"correo": correo, "password": password})
    return _login
