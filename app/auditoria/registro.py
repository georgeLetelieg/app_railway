"""Registro de auditoría: guarda quién corrigió o eliminó un dato, cuándo, y cómo era antes.

Se llama dentro de la misma transacción del cambio (antes del commit), de modo que el cambio y su
registro se guardan juntos o no se guarda ninguno. La tabla `auditoria` es de solo inserción
(triggers en el script SQL impiden modificarla o borrarla).
"""
import json

from flask import session

from app.utils import ahora

ACCION_MODIFICACION = "Modificación"
ACCION_ELIMINACION = "Eliminación"
TABLAS = {"carga_combustible": "Combustible", "registro_mantencion": "Mantenciones",
          "maquinaria": "Datos base de maquinaria", "usuario": "Usuarios (cambios de estado)"}


def _json(datos):
    return json.dumps(datos, ensure_ascii=False, default=str, sort_keys=True)


def registrar(cur, tabla, registro_id, accion, antes, despues=None, maquinaria_id=None):
    cur.execute(
        "INSERT INTO auditoria (Tabla_Afectada, ID_Registro, FK_ID_Maquinaria, Accion, FK_ID_Usuario, "
        "Fecha_Hora, Datos_Anteriores, Datos_Nuevos) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (tabla, registro_id, maquinaria_id, accion, session["usuario_id"], ahora(), _json(antes),
         _json(despues) if despues is not None else None))


def cambios(antes, despues):
    """Lista de (campo, valor_antes, valor_despues). En una eliminación devuelve todos los campos."""
    if despues is None:
        return [(campo, valor, None) for campo, valor in antes.items()]
    return [(campo, antes.get(campo), despues.get(campo)) for campo in despues
            if antes.get(campo) != despues.get(campo)]
