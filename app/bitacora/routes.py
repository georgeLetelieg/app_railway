"""Bitácora operativa (RF2): registro diario de cada equipo, equivalente al «Parte diario de equipo» en papel.

Cada registro guarda el horómetro (inicio y final), el kilometraje, el estado del día y los datos del
parte: N° de folio, faena, operador, jornada (turno, hora de comienzo y de término), tipo de trabajo,
observaciones y combustible (litros, del tipo que use cada equipo).

El historial es inalterable: NO existen rutas para editar ni eliminar registros
(la base de datos además lo impide con triggers, ver el script de creación en database/).
"""
from contextlib import closing
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

import pymysql
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app import db
from app.bitacora.queries import dias_sin_registro, ultimas_lecturas
from app.maquinaria.routes import _obtener
from app.security import escritura_required, login_required
from app.utils import ahora, parse_numero_es

bp = Blueprint("bitacora", __name__, url_prefix="/maquinaria/<int:maquinaria_id>/bitacora")

POR_PAGINA = 30
MAXIMO = Decimal("99999999.99")
SUGERENCIAS_ESTADO = ("Operativo", "Día en taller", "Sin actividad", "Fin de semana / feriado",
                      "Detenida por clima", "Sin operador")
SUGERENCIAS_TURNO = ("Día", "Noche", "Mañana", "Tarde")
CAMPOS = (("Horometro", "horómetro"), ("Kilometraje", "kilometraje"))

# Campos de texto del parte diario: (clave del formulario, etiqueta, largo máximo)
TEXTOS_PARTE = (("numero_parte", "N° de parte", 20), ("faena", "faena", 150), ("operador", "operador", 150),
                ("turno", "turno", 30), ("tipo_trabajo", "tipo de trabajo", 500),
                ("observaciones", "observaciones", 500))
CLAVES_FORM = ("fecha", "horometro_inicio", "horometro", "kilometraje", "estado_diario", "hora_comienzo",
               "hora_termino", "combustible") + tuple(c for c, _, _ in TEXTOS_PARTE)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _parse_fecha(texto):
    try:
        return date.fromisoformat(texto)
    except (ValueError, TypeError):
        return None


def _leer_decimal(texto, etiqueta, errores):
    if not (texto or "").strip():
        return None
    try:
        valor = parse_numero_es(texto)
    except InvalidOperation:
        errores.append(f"El {etiqueta} debe ser un número (usa coma para los decimales).")
        return None
    if not valor.is_finite() or valor < 0 or valor > MAXIMO:
        errores.append(f"El {etiqueta} debe estar entre 0 y {MAXIMO}.")
        return None
    return valor.quantize(Decimal("0.01"))


def _leer_texto(texto, etiqueta, maximo, errores):
    """Texto opcional: recorta y colapsa espacios. Devuelve None si queda vacío."""
    valor = " ".join((texto or "").split())
    if len(valor) > maximo:
        errores.append(f"El campo «{etiqueta}» admite como máximo {maximo} caracteres.")
        return None
    return valor or None


def _leer_hora(texto, etiqueta, errores):
    """Hora opcional HH:MM. Se devuelve como texto 'HH:MM:SS' (formato que acepta la columna TIME)."""
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return time.fromisoformat(texto).replace(second=0, microsecond=0).strftime("%H:%M:%S")
    except ValueError:
        errores.append(f"La hora de {etiqueta} no es válida (usa el formato HH:MM).")
        return None


def _hhmm(valor):
    """Muestra una columna TIME como HH:MM (PyMySQL la entrega como timedelta)."""
    if valor is None or valor == "":
        return ""
    if isinstance(valor, timedelta):
        minutos = int(valor.total_seconds()) // 60
        return f"{minutos // 60:02d}:{minutos % 60:02d}"
    if isinstance(valor, (time, datetime)):
        return valor.strftime("%H:%M")
    return str(valor)[:5]


def _validar_secuencia(cur, maquinaria_id, fecha, campo, etiqueta, valor, errores):
    """Las lecturas no pueden retroceder: deben ser >= a la anterior y <= a la posterior."""
    if valor is None:
        return
    for operador, orden, texto, comparar in (
            ("<", "DESC", "anterior", lambda v, o: v < o),
            (">", "ASC", "posterior", lambda v, o: v > o)):
        cur.execute(
            f"SELECT {campo} FROM bitacora_operativa WHERE FK_ID_Maquinaria = %s "
            f"AND Fecha_Registro {operador} %s AND {campo} IS NOT NULL "
            f"ORDER BY {campo} {orden} LIMIT 1", (maquinaria_id, fecha))
        fila = cur.fetchone()
        if fila is not None:
            otro = Decimal(str(fila[campo]))
            if comparar(valor, otro):
                relacion = "menor" if texto == "anterior" else "mayor"
                errores.append(f"El {etiqueta} ({valor:.2f}) no puede ser {relacion} que el registro "
                               f"{texto} ({otro:.2f}); las lecturas no pueden retroceder.")


def _formulario(maquina, datos, lecturas, errores, codigo=200):
    for error in errores:
        flash(error, "danger")
    return render_template("bitacora/form.html", maquina=maquina, datos=datos, lecturas=lecturas,
                           hoy=ahora().date().isoformat(), sugerencias=SUGERENCIAS_ESTADO,
                           turnos=SUGERENCIAS_TURNO), codigo


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def lista(maquinaria_id):
    desde = _parse_fecha(request.args.get("desde", ""))
    hasta = _parse_fecha(request.args.get("hasta", ""))
    pagina = max(1, request.args.get("pagina", 1, type=int))

    condiciones, params = ["b.FK_ID_Maquinaria = %s"], [maquinaria_id]
    if desde:
        condiciones.append("b.Fecha_Registro >= %s")
        params.append(desde)
    if hasta:
        condiciones.append("b.Fecha_Registro <= %s")
        params.append(hasta)
    donde = " AND ".join(condiciones)

    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        cur.execute(f"SELECT COUNT(*) AS n FROM bitacora_operativa b WHERE {donde}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            "SELECT b.ID_Bitacora, b.Fecha_Registro, b.Fecha_Hora_Ingreso, b.Horometro, b.Kilometraje, "
            "b.Estado_Diario, b.Numero_Parte, b.Faena, b.Operador, b.Horometro_Inicio, b.Horas_Trabajadas, "
            "b.Turno, b.Hora_Comienzo, b.Hora_Termino, b.Tipo_Trabajo, b.Observaciones, b.Combustible_Litros, "
            "u.Nombre_Completo "
            "FROM bitacora_operativa b JOIN usuario u ON u.ID_Usuario = b.FK_ID_Usuario "
            f"WHERE {donde} ORDER BY b.Fecha_Registro DESC LIMIT %s OFFSET %s",
            params + [POR_PAGINA, (pagina - 1) * POR_PAGINA])
        registros = [{**r, "comienzo": _hhmm(r["Hora_Comienzo"]), "termino": _hhmm(r["Hora_Termino"])}
                     for r in cur.fetchall()]
        lecturas = ultimas_lecturas(cur, maquinaria_id)
        faltantes = dias_sin_registro(cur, maquinaria_id, ahora().date())

    paginas = max(1, -(-total // POR_PAGINA))
    return render_template("bitacora/lista.html", maquina=maquina, registros=registros, total=total,
                           lecturas=lecturas, faltantes=faltantes, pagina=pagina, paginas=paginas,
                           desde=desde.isoformat() if desde else "", hasta=hasta.isoformat() if hasta else "")


@bp.route("/nuevo", methods=["GET", "POST"])
@login_required
@escritura_required
def nuevo(maquinaria_id):
    hoy = ahora().date()
    with closing(db.get_db_connection()) as conn, conn.cursor() as cur:
        maquina = _obtener(cur, maquinaria_id)
        lecturas = ultimas_lecturas(cur, maquinaria_id)

        if request.method == "GET":
            fecha = _parse_fecha(request.args.get("fecha", "")) or hoy
            datos = {"fecha": min(fecha, hoy).isoformat()}
            if lecturas["horometro"]:      # el horómetro de inicio suele ser el final del último registro
                datos["horometro_inicio"] = f"{lecturas['horometro']['valor']:.2f}".replace(".", ",")
            return _formulario(maquina, datos, lecturas, [])

        form = request.form
        datos = {clave: form.get(clave, "") for clave in CLAVES_FORM}
        errores = []

        fecha = _parse_fecha(datos["fecha"])
        if fecha is None:
            errores.append("Indica una fecha válida.")
        elif fecha > hoy:
            errores.append("La fecha del registro no puede ser futura.")
        horometro_inicio = _leer_decimal(datos["horometro_inicio"], "horómetro de inicio", errores)
        horometro = _leer_decimal(datos["horometro"], "horómetro final", errores)
        kilometraje = _leer_decimal(datos["kilometraje"], "kilometraje", errores)
        combustible = _leer_decimal(datos["combustible"], "combustible (litros)", errores)
        estado = datos["estado_diario"].strip()
        if not estado or len(estado) > 255:
            errores.append("El estado del día es obligatorio (máximo 255 caracteres).")
        textos = {clave: _leer_texto(datos[clave], etiqueta, maximo, errores)
                  for clave, etiqueta, maximo in TEXTOS_PARTE}
        hora_comienzo = _leer_hora(datos["hora_comienzo"], "comienzo", errores)
        hora_termino = _leer_hora(datos["hora_termino"], "término", errores)

        if horometro_inicio is not None:
            if horometro is None:
                errores.append("Si indicas el horómetro de inicio, indica también el horómetro final.")
            elif horometro < horometro_inicio:
                errores.append("El horómetro final no puede ser menor que el horómetro de inicio.")

        if not errores:
            cur.execute("SELECT 1 AS x FROM bitacora_operativa WHERE FK_ID_Maquinaria = %s "
                        "AND Fecha_Registro = %s", (maquinaria_id, fecha))
            if cur.fetchone():
                errores.append("Ya existe un registro para esa fecha. La bitácora es inalterable: "
                               "no admite modificaciones ni segundos registros del mismo día.")
            if textos["numero_parte"]:
                cur.execute("SELECT 1 AS x FROM bitacora_operativa WHERE Numero_Parte = %s",
                            (textos["numero_parte"],))
                if cur.fetchone():
                    errores.append(f"El parte N° {textos['numero_parte']} ya está registrado.")
        if not errores:
            for (campo, etiqueta), valor in zip(CAMPOS, (horometro, kilometraje)):
                _validar_secuencia(cur, maquinaria_id, fecha, campo, etiqueta, valor, errores)
            # El horómetro de inicio tampoco puede quedar bajo la lectura final del día anterior
            _validar_secuencia(cur, maquinaria_id, fecha, "Horometro", "horómetro de inicio",
                               horometro_inicio, errores)
            # ...y el final de este día no puede superar el inicio de un día posterior
            _validar_secuencia(cur, maquinaria_id, fecha, "Horometro_Inicio", "horómetro final",
                               horometro, errores)
        if errores:
            return _formulario(maquina, datos, lecturas, list(dict.fromkeys(errores)), 400)

        try:
            cur.execute(
                "INSERT INTO bitacora_operativa (FK_ID_Maquinaria, FK_ID_Usuario, Fecha_Registro, "
                "Fecha_Hora_Ingreso, Horometro, Kilometraje, Estado_Diario, Numero_Parte, Faena, Operador, "
                "Horometro_Inicio, Turno, Hora_Comienzo, Hora_Termino, Tipo_Trabajo, Observaciones, "
                "Combustible_Litros) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (maquinaria_id, session["usuario_id"], fecha, ahora(), horometro, kilometraje, estado,
                 textos["numero_parte"], textos["faena"], textos["operador"], horometro_inicio, textos["turno"],
                 hora_comienzo, hora_termino, textos["tipo_trabajo"], textos["observaciones"], combustible))
            conn.commit()
        except pymysql.err.IntegrityError:
            conn.rollback()
            return _formulario(maquina, datos, lecturas,
                               ["Ya existe un registro para esa fecha o ese N° de parte."], 400)

    flash(f"Registro del {fecha.strftime('%d-%m-%Y')} guardado. Ya no podrá modificarse.", "success")
    return redirect(url_for("bitacora.lista", maquinaria_id=maquinaria_id))
