"""Comandos de consola:  flask --app run crear-usuario"""
from contextlib import closing

import click
from flask import current_app
from werkzeug.security import generate_password_hash

from app import db
from app.usuarios import queries as q
from app.usuarios.validacion import (normalizar_correo, normalizar_nombre, validar_contrasena, validar_correo,
                                     validar_nombre)


@click.command("crear-usuario")
@click.option("--nombre", prompt="Nombre completo")
@click.option("--correo", prompt="Correo")
@click.option("--rol", prompt="Rol (ej: Super Administrador)")
@click.password_option("--password", prompt="Contraseña")
def crear_usuario(nombre, correo, rol, password):
    """Crea un usuario (necesario para el primer acceso). Aplica la misma política que la gestión web."""
    nombre, correo = normalizar_nombre(nombre), normalizar_correo(correo)
    errores = [e for e in (validar_nombre(nombre), validar_correo(correo)) if e] + validar_contrasena(password, correo, nombre)
    if errores:
        raise click.ClickException(" ".join(errores))
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        cur.execute("SELECT ID_Rol FROM rol WHERE Nombre_Rol = %s", (rol,))
        fila = cur.fetchone()
        if not fila:
            raise click.ClickException(f"El rol '{rol}' no existe en la tabla rol.")
        if q.existe_correo(cur, correo):
            raise click.ClickException("Ya existe un usuario con ese correo.")
        q.insertar_usuario(cur, nombre, correo,
                           generate_password_hash(password, method=current_app.config["METODO_HASH"]), fila["ID_Rol"])
        conn.commit()
    click.echo(f"Usuario '{correo}' creado con el rol '{rol}'.")
