def contar_documentos(cur, maquinaria_id):
    cur.execute("SELECT COUNT(*) AS n FROM documento WHERE FK_ID_Maquinaria = %s", (maquinaria_id,))
    return cur.fetchone()["n"]
