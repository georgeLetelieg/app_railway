def ultima_mantencion(cur, maquinaria_id):
    cur.execute("SELECT Fecha_Mantencion, Horometro_Mecanico FROM registro_mantencion "
                "WHERE FK_ID_Maquinaria = %s ORDER BY Fecha_Mantencion DESC, ID_Registro DESC LIMIT 1",
                (maquinaria_id,))
    return cur.fetchone()


def contar_mantenciones(cur, maquinaria_id):
    cur.execute("SELECT COUNT(*) AS n FROM registro_mantencion WHERE FK_ID_Maquinaria = %s", (maquinaria_id,))
    return cur.fetchone()["n"]
