"""Consultas SQL de la gestión de usuarios. Solo SQL: la lógica de negocio vive en routes.py."""

_SELECT_USUARIO = (
    "SELECT u.ID_Usuario, u.Nombre_Completo, u.Correo, u.Estado_Activo, u.Intentos_Fallidos, "
    "u.Fecha_Hora_Bloqueo, r.ID_Rol, r.Nombre_Rol "
    "FROM usuario u JOIN rol r ON r.ID_Rol = u.FK_ID_Rol ")


def listar_usuarios(cur):
    cur.execute(_SELECT_USUARIO + "ORDER BY u.Nombre_Completo, u.ID_Usuario")
    return cur.fetchall()


def obtener_usuario(cur, usuario_id):
    cur.execute(_SELECT_USUARIO + "WHERE u.ID_Usuario = %s", (usuario_id,))
    return cur.fetchone()


def listar_roles(cur):
    cur.execute("SELECT ID_Rol, Nombre_Rol FROM rol ORDER BY ID_Rol")
    return cur.fetchall()


def existe_correo(cur, correo):
    cur.execute("SELECT 1 AS x FROM usuario WHERE Correo = %s", (correo,))
    return cur.fetchone() is not None


def insertar_usuario(cur, nombre, correo, contrasena_hash, rol_id):
    """Inserta un usuario ACTIVO y sin intentos fallidos. Recibe la contraseña ya encriptada (hash)."""
    cur.execute(
        "INSERT INTO usuario (Nombre_Completo, Correo, Contrasena_Hash, Estado_Activo, Intentos_Fallidos, FK_ID_Rol) "
        "VALUES (%s, %s, %s, 1, 0, %s)", (nombre, correo, contrasena_hash, rol_id))
    return cur.lastrowid


def inactivar(cur, usuario_id):
    cur.execute("UPDATE usuario SET Estado_Activo = 0 WHERE ID_Usuario = %s", (usuario_id,))


def reactivar(cur, usuario_id):
    """Activa la cuenta y limpia cualquier bloqueo previo, para que no quede bloqueada al volver."""
    cur.execute("UPDATE usuario SET Estado_Activo = 1, Intentos_Fallidos = 0, Fecha_Hora_Bloqueo = NULL "
                "WHERE ID_Usuario = %s", (usuario_id,))


def desbloquear(cur, usuario_id):
    cur.execute("UPDATE usuario SET Intentos_Fallidos = 0, Fecha_Hora_Bloqueo = NULL WHERE ID_Usuario = %s",
                (usuario_id,))
