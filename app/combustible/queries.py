def ultima_carga(cur, maquinaria_id):
    cur.execute("SELECT Fecha_Carga, Litros_Cargados FROM carga_combustible "
                "WHERE FK_ID_Maquinaria = %s ORDER BY Fecha_Carga DESC LIMIT 1", (maquinaria_id,))
    return cur.fetchone()
