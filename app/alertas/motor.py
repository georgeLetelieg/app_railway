"""Motor de alertas estáticas (RF5).

Evalúa, para cada maquinaria, los tres vencimientos obligatorios:
  - Revisión técnica y permiso de circulación (fechas): aviso a 30 días o menos.
  - Próxima mantención preventiva (horómetro objetivo): aviso a 50 horas o menos,
    calculado contra la última lectura de horómetro registrada en la bitácora.
Las alertas personalizadas activas (RF6) se suman a la evaluación de cada maquinaria:
aviso a 7 días o menos (configurable) y «vencida» desde el día siguiente a su fecha.

El semáforo se calcula al consultar (no se guarda), porque cambia solo con el paso del
tiempo. Prioridad: rojo (algo vencido) > amarillo (algo por vencer) > incompleto
(faltan datos) > verde. Las maquinarias «Inactiva» no generan avisos.
"""
from decimal import Decimal

from flask import current_app, g

from app.utils import ahora, num_es

CONCEPTOS = (("revision_tecnica", "Revisión técnica"),
             ("permiso_circulacion", "Permiso de circulación"),
             ("mantencion", "Próxima mantención preventiva"))
ORDEN_ESTADO = {"vencida": 0, "por_vencer": 1, "sin_dato": 2, "vigente": 3}

SQL_FLOTA = (
    "SELECT m.ID_Maquinaria, m.Patente, m.Modelo, m.Estado_Operativo_Actual, "
    "a.Vencimiento_Revision_Tecnica, a.Vencimiento_Permiso_Circulacion, a.Proxima_Mantencion_Preventiva, "
    "(SELECT b.Horometro FROM bitacora_operativa b WHERE b.FK_ID_Maquinaria = m.ID_Maquinaria "
    "AND b.Horometro IS NOT NULL ORDER BY b.Fecha_Registro DESC LIMIT 1) AS horometro "
    "FROM maquinaria m LEFT JOIN alerta_estatica a ON a.FK_ID_Maquinaria = m.ID_Maquinaria ")


def _item_fecha(clave, nombre, vencimiento, hoy, dias_aviso):
    item = {"clave": clave, "nombre": nombre, "valor_texto": "—", "restante": None}
    if vencimiento is None:
        return {**item, "estado": "sin_dato", "detalle": "Sin registrar"}
    dias = (vencimiento - hoy).days
    item.update(valor_texto=vencimiento.strftime("%d-%m-%Y"), restante=dias)
    if dias < 0:
        return {**item, "estado": "vencida", "detalle": f"Vencida hace {-dias} día(s)"}
    if dias == 0:
        return {**item, "estado": "por_vencer", "detalle": "Vence hoy"}
    estado = "por_vencer" if dias <= dias_aviso else "vigente"
    return {**item, "estado": estado, "detalle": f"Vence en {dias} día(s)"}


def _item_personalizada(fila, hoy, dias_aviso):
    item = _item_fecha(f"personalizada-{fila['ID_Alerta_Personalizada']}", fila["Titulo_Alerta"],
                       fila["Fecha_Vencimiento"], hoy, dias_aviso)
    return {**item, "personalizada": True, "alerta_id": fila["ID_Alerta_Personalizada"]}


def _item_mantencion(meta, horometro, horas_aviso):
    item = {"clave": "mantencion", "nombre": "Próxima mantención preventiva", "valor_texto": "—", "restante": None}
    if meta is None:
        return {**item, "estado": "sin_dato", "detalle": "Sin meta registrada"}
    meta = Decimal(str(meta))
    item["valor_texto"] = f"{num_es(meta)} h"
    if horometro is None:
        return {**item, "estado": "sin_dato", "detalle": "Sin lecturas de horómetro en la bitácora"}
    restantes = meta - Decimal(str(horometro))
    item["restante"] = restantes
    if restantes < 0:
        return {**item, "estado": "vencida", "detalle": f"Excedida por {num_es(-restantes)} h"}
    estado = "por_vencer" if restantes <= horas_aviso else "vigente"
    return {**item, "estado": estado, "detalle": f"Faltan {num_es(restantes)} h (horómetro actual {num_es(horometro)} h)"}


def evaluar(fila, hoy, dias_aviso=30, horas_aviso=50, personalizadas=(), dias_personalizadas=7):
    items = [
        _item_fecha("revision_tecnica", "Revisión técnica", fila["Vencimiento_Revision_Tecnica"], hoy, dias_aviso),
        _item_fecha("permiso_circulacion", "Permiso de circulación", fila["Vencimiento_Permiso_Circulacion"], hoy, dias_aviso),
        _item_mantencion(fila["Proxima_Mantencion_Preventiva"], fila["horometro"], horas_aviso),
    ]
    items += [_item_personalizada(p, hoy, dias_personalizadas) for p in personalizadas]
    estados = {i["estado"] for i in items}
    activa = fila["Estado_Operativo_Actual"] != "Inactiva"
    if not activa:
        semaforo = None
    elif "vencida" in estados:
        semaforo = "rojo"
    elif "por_vencer" in estados:
        semaforo = "amarillo"
    elif "sin_dato" in estados:
        semaforo = "incompleto"
    else:
        semaforo = "verde"
    return {**{k: fila[k] for k in ("ID_Maquinaria", "Patente", "Modelo", "Estado_Operativo_Actual")},
            "items": items, "semaforo": semaforo, "activa": activa}


def _umbrales():
    c = current_app.config
    return {"dias_aviso": c["ALERTA_DIAS_DOCUMENTOS"], "horas_aviso": c["ALERTA_HORAS_MANTENCION"],
            "dias_personalizadas": c["ALERTA_DIAS_PERSONALIZADAS"]}


SQL_PERSONALIZADAS = ("SELECT ID_Alerta_Personalizada, FK_ID_Maquinaria, Titulo_Alerta, Fecha_Vencimiento "
                      "FROM alerta_personalizada WHERE Estado_Activa = 1 ")


def evaluar_una(cur, maquinaria_id):
    cur.execute(SQL_FLOTA + "WHERE m.ID_Maquinaria = %s", (maquinaria_id,))
    fila = cur.fetchone()
    if not fila:
        return None
    cur.execute(SQL_PERSONALIZADAS + "AND FK_ID_Maquinaria = %s ORDER BY Fecha_Vencimiento", (maquinaria_id,))
    return evaluar(fila, ahora().date(), personalizadas=cur.fetchall(), **_umbrales())


def flota_alertas():
    """Evaluación de toda la flota (se calcula una sola vez por petición)."""
    if "alertas_flota" not in g:
        from contextlib import closing
        from app import db
        with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
            cur.execute(SQL_FLOTA + "ORDER BY m.Patente")
            filas = cur.fetchall()
            cur.execute(SQL_PERSONALIZADAS + "ORDER BY Fecha_Vencimiento")
            personalizadas = cur.fetchall()
        por_maquina = {}
        for p in personalizadas:
            por_maquina.setdefault(p["FK_ID_Maquinaria"], []).append(p)
        hoy = ahora().date()
        g.alertas_flota = [evaluar(f, hoy, personalizadas=por_maquina.get(f["ID_Maquinaria"], ()), **_umbrales())
                           for f in filas]
    return g.alertas_flota


def personalizadas_de_maquina(cur, maquinaria_id, max_cumplidas=10):
    """Alertas personalizadas de una maquinaria para mostrarlas en su perfil.
    Devuelve {'activas': [...], 'cumplidas': [...]} con su estado ya evaluado."""
    cur.execute("SELECT p.ID_Alerta_Personalizada, p.Titulo_Alerta, p.Fecha_Vencimiento, p.Estado_Activa, "
                "u.Nombre_Completo FROM alerta_personalizada p JOIN usuario u ON u.ID_Usuario = p.FK_ID_Usuario "
                "WHERE p.FK_ID_Maquinaria = %s ORDER BY p.Fecha_Vencimiento", (maquinaria_id,))
    hoy, dias = ahora().date(), current_app.config["ALERTA_DIAS_PERSONALIZADAS"]
    activas, cumplidas = [], []
    for fila in cur.fetchall():
        if fila["Estado_Activa"]:
            item = _item_fecha("", fila["Titulo_Alerta"], fila["Fecha_Vencimiento"], hoy, dias)
            activas.append({**fila, "estado": item["estado"], "detalle": item["detalle"]})
        else:
            cumplidas.append({**fila, "estado": "cumplida", "detalle": ""})
    return {"activas": activas, "cumplidas": sorted(cumplidas, key=lambda f: f["Fecha_Vencimiento"],
                                                    reverse=True)[:max_cumplidas]}


def alertas_ordenadas(flota, incluir_sin_dato=False):
    """Alertas de maquinarias activas, de la más urgente a la menos urgente."""
    estados = ("vencida", "por_vencer") + (("sin_dato",) if incluir_sin_dato else ())
    lista = [{**item, "maquina": m} for m in flota if m["activa"] for item in m["items"] if item["estado"] in estados]
    lista.sort(key=lambda a: (ORDEN_ESTADO[a["estado"]], a["restante"] if a["restante"] is not None else 10 ** 9,
                              a["maquina"]["Patente"]))
    return lista


def resumen(flota):
    """Contadores de alertas.

    - Por ALERTA (claves n_*): cuántas alertas hay en cada estado, de todas las maquinarias activas. Es lo que
      muestran las tarjetas del centro de alertas, y coincide con las filas de su tabla.
    - Por MAQUINARIA (rojo, amarillo, incompleto, verde): cuántas maquinarias activas tienen cada semáforo
      (su peor estado). Una máquina con una alerta vencida y otra por vencer cuenta una sola vez.
    - `alertas` = vencidas + por vencer: es el número del menú.
    """
    activas = [m for m in flota if m["activa"]]
    por_maquina = {s: sum(1 for m in activas if m["semaforo"] == s) for s in ("rojo", "amarillo", "incompleto", "verde")}
    items = [i for m in activas for i in m["items"]]
    n = {e: sum(1 for i in items if i["estado"] == e) for e in ("vencida", "por_vencer", "sin_dato", "vigente")}
    return {**por_maquina, "maquinas": len(activas),
            "n_vencidas": n["vencida"], "n_por_vencer": n["por_vencer"],
            "n_sin_dato": n["sin_dato"], "n_vigentes": n["vigente"],
            "alertas": n["vencida"] + n["por_vencer"], "hay_vencidas": n["vencida"] > 0}


def semaforos(flota):
    return {m["ID_Maquinaria"]: m["semaforo"] for m in flota}
