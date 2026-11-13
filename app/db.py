import pymysql
from flask import current_app


def get_db_connection():
    """Abre una conexión nueva. Usar siempre con contextlib.closing()."""
    c = current_app.config
    return pymysql.connect(
        host=c["DB_HOST"], port=c["DB_PORT"], user=c["DB_USER"],
        password=c["DB_PASSWORD"], database=c["DB_NAME"],
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )
