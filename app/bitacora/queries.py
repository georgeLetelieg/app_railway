"""Consultas compartidas de la bitácora (las usa también el perfil de la maquinaria)."""
from datetime import timedelta

VENTANA_DIAS = 30


def _ultima(cur, maquinaria_id, campo):
    cur.execute(
        f"SELECT {campo}, Fecha_Registro FROM bitacora_operativa "
        f"WHERE FK_ID_Maquinaria = %s AND {campo} IS NOT NULL "
        "ORDER BY Fecha_Registro DESC LIMIT 1", (maquinaria_id,))
    fila = cur.fetchone()
    return {"valor": fila[campo], "fecha": fila["Fecha_Registro"]} if fila else None


def ultimas_lecturas(cur, maquinaria_id):
    """Última lectura registrada de horómetro y de kilometraje (o None)."""
    return {"horometro": _ultima(cur, maquinaria_id, "Horometro"),
            "kilometraje": _ultima(cur, maquinaria_id, "Kilometraje")}


def dias_sin_registro(cur, maquinaria_id, hoy):
    """Días (del más reciente al más antiguo) sin registro, desde el primer registro
    de la máquina y dentro de los últimos 30 días. El día de hoy no cuenta."""
    cur.execute("SELECT Fecha_Registro FROM bitacora_operativa WHERE FK_ID_Maquinaria = %s "
                "ORDER BY Fecha_Registro ASC LIMIT 1", (maquinaria_id,))
    primero = cur.fetchone()
    if primero is None:
        return []
    desde = max(primero["Fecha_Registro"], hoy - timedelta(days=VENTANA_DIAS - 1))
    cur.execute("SELECT Fecha_Registro FROM bitacora_operativa "
                "WHERE FK_ID_Maquinaria = %s AND Fecha_Registro >= %s", (maquinaria_id, desde))
    registrados = {fila["Fecha_Registro"] for fila in cur.fetchall()}
    dias = (hoy - timedelta(days=i) for i in range(1, (hoy - desde).days + 1))
    return [d for d in dias if d not in registrados]
