"""Generador de la ficha técnica en PDF (RF7). Función pura: recibe datos y devuelve los bytes del PDF."""
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.utils import num_es

AZUL = colors.HexColor("#2f3e4e")
GRIS_CLARO = colors.HexColor("#f1f3f5")
COLOR_ESTADO = {"vencida": "#b02a37", "por_vencer": "#b26a00", "vigente": "#1e7b34", "sin_dato": "#6c757d"}
TEXTO_ESTADO = {"vencida": "VENCIDA", "por_vencer": "POR VENCER", "vigente": "Vigente", "sin_dato": "Sin registrar"}
TEXTO_SEMAFORO = {"rojo": "Con vencimientos", "amarillo": "Por vencer", "incompleto": "Datos incompletos",
                  "verde": "Al día", None: "No aplica (maquinaria inactiva)"}


def _esc(valor):
    """Escapa texto de usuario: Paragraph interpreta marcado tipo XML (p. ej. <img src=...>)."""
    return escape("" if valor is None else str(valor))


MAX_CARACTERES_DETALLE, MAX_LINEAS_DETALLE = 1500, 25   # una celda no puede partirse entre páginas


def _texto_largo(texto):
    """Texto libre de la mantención: escapado, con saltos de línea y recortado para caber en una página."""
    lineas = texto.split("\n")
    recortado = len(lineas) > MAX_LINEAS_DETALLE or len(texto) > MAX_CARACTERES_DETALLE
    html = _esc("\n".join(lineas[:MAX_LINEAS_DETALLE])[:MAX_CARACTERES_DETALLE]).replace("\n", "<br/>")
    return html + ("<br/><i>[…] Texto completo disponible en el sistema.</i>" if recortado else "")


def _fecha(valor):
    return valor.strftime("%d-%m-%Y") if valor else "—"


class _LienzoNumerado(canvas.Canvas):
    """Canvas que dibuja el pie de página con «Página X de Y»."""

    def __init__(self, *args, pie="", **kwargs):
        super().__init__(*args, **kwargs)
        self._estados, self._pie = [], pie

    def showPage(self):
        self._estados.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._estados)
        for estado in self._estados:
            self.__dict__.update(estado)
            ancho, _ = letter
            self.setStrokeColor(colors.HexColor("#adb5bd"))
            self.line(2 * cm, 1.6 * cm, ancho - 2 * cm, 1.6 * cm)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(colors.HexColor("#495057"))
            self.drawString(2 * cm, 1.15 * cm, self._pie)
            self.drawRightString(ancho - 2 * cm, 1.15 * cm, f"Página {self._pageNumber} de {total}")
            super().showPage()
        super().save()


def generar_ficha(d):
    m = d["maquina"]
    estilos = getSampleStyleSheet()
    base = ParagraphStyle("base", parent=estilos["Normal"], fontName="Helvetica", fontSize=9.5, leading=12.5,
                          alignment=TA_LEFT, wordWrap="CJK")           # CJK: corta palabras muy largas
    celda = ParagraphStyle("celda", parent=base, fontSize=8.5, leading=10.5)
    celda_h = ParagraphStyle("celda_h", parent=celda, fontName="Helvetica-Bold", textColor=colors.white)
    titulo = ParagraphStyle("titulo", parent=base, fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=AZUL)
    sub = ParagraphStyle("sub", parent=base, fontSize=10, textColor=colors.HexColor("#495057"))
    h2 = ParagraphStyle("h2", parent=base, fontName="Helvetica-Bold", fontSize=11.5, leading=14, textColor=AZUL,
                        spaceBefore=14, spaceAfter=5, keepWithNext=True)
    nota = ParagraphStyle("nota", parent=base, fontSize=8, leading=10, textColor=colors.HexColor("#6c757d"))

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                            bottomMargin=2.2 * cm, title=f"Ficha técnica {m['Patente']}",
                            author=d["empresa"], subject="Ficha técnica de maquinaria")
    ancho = doc.width

    def tabla(filas, anchos, zebra=True, cabecera=True):
        t = Table(filas, colWidths=[ancho * a for a in anchos], repeatRows=1 if cabecera else 0)
        est = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#ced4da")),
               ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if cabecera:
            est.append(("BACKGROUND", (0, 0), (-1, 0), AZUL))
        if zebra:
            est += [("ROWBACKGROUNDS", (0, 1 if cabecera else 0), (-1, -1), [colors.white, GRIS_CLARO])]
        t.setStyle(TableStyle(est))
        return t

    historia = []
    # ---- Encabezado
    historia += [Paragraph("FICHA TÉCNICA DE MAQUINARIA", titulo),
                 Paragraph(_esc(d["empresa"]), sub),
                 Paragraph(f"Emitida el {d['emitido_en']:%d-%m-%Y a las %H:%M} por {_esc(d['emitido_por'])}", nota),
                 Spacer(1, 4)]

    # ---- Identificación
    historia.append(Paragraph("1. Identificación", h2))
    foto = Paragraph("Sin fotografía", celda)
    if d.get("foto_path"):
        try:
            foto = Image(str(d["foto_path"]), width=6.3 * cm, height=4.8 * cm, kind="proportional")
        except Exception:      # archivo dañado o ilegible: se informa en lugar de fallar
            foto = Paragraph("Fotografía no disponible", celda)
    lec = d["lecturas"]
    ultima = lambda x, u: (f"{num_es(x['valor'])} {u} (al {_fecha(x['fecha'])})" if x else "Sin lecturas")
    datos = [("Patente", m["Patente"]), ("Modelo", m["Modelo"]), ("Año", m["Anio"]),
             ("Tipo de combustible", m["Tipo_Combustible"]), ("N° de chasis", m["Numero_Chasis"]),
             ("Estado operativo actual", m["Estado_Operativo_Actual"]),
             ("Último horómetro", ultima(lec["horometro"], "h")), ("Último kilometraje", ultima(lec["kilometraje"], "km"))]
    tabla_datos = Table([[Paragraph(f"<b>{_esc(k)}</b>", celda), Paragraph(_esc(v), celda)] for k, v in datos],
                        colWidths=[3.9 * cm, ancho - 6.9 * cm - 3.9 * cm])
    tabla_datos.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dee2e6")),
                                     ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    bloque = Table([[foto, tabla_datos]], colWidths=[6.9 * cm, ancho - 6.9 * cm])
    bloque.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    historia.append(bloque)

    # ---- Alertas
    a = d["alertas"]
    historia.append(Paragraph("2. Alertas y vencimientos", h2))
    historia.append(Paragraph(f"<b>Estado general:</b> {_esc(TEXTO_SEMAFORO[a['semaforo']])}", base))
    historia.append(Spacer(1, 4))
    filas = [[Paragraph(t, celda_h) for t in ("Concepto", "Vencimiento / meta", "Estado", "Detalle")]]
    for it in a["items"]:
        nombre = _esc(it["nombre"]) + (" <i>(personalizada)</i>" if it.get("personalizada") else "")
        color = COLOR_ESTADO[it["estado"]]
        filas.append([Paragraph(nombre, celda), Paragraph(_esc(it["valor_texto"]), celda),
                      Paragraph(f'<font color="{color}"><b>{TEXTO_ESTADO[it["estado"]]}</b></font>', celda),
                      Paragraph(_esc(it["detalle"]), celda)])
    historia.append(tabla(filas, [0.30, 0.17, 0.14, 0.39]))
    if not a["activa"]:
        historia.append(Paragraph("Maquinaria inactiva: no se incluye en los avisos generales del sistema.", nota))

    # ---- Historial operativo
    historia.append(Paragraph("3. Historial operativo (bitácora)", h2))
    historia.append(Paragraph(_esc(d["periodo_texto"]), nota))
    historia.append(Spacer(1, 3))
    if d["bitacora"]:
        filas = [[Paragraph(t, celda_h) for t in ("Fecha / N° parte", "Horómetro (h)", "Horas", "Km",
                                                  "Estado del día / faena", "Operador / ingresó")]]
        for r in d["bitacora"]:
            fecha = _fecha(r["Fecha_Registro"]) + (f"<br/>N° {_esc(r['Numero_Parte'])}" if r.get("Numero_Parte") else "")
            estado = _esc(r["Estado_Diario"]) + (f"<br/><i>{_esc(r['Faena'])}</i>" if r.get("Faena") else "")
            persona = (f"{_esc(r['Operador'])}<br/>" if r.get("Operador") else "") + _esc(r["Nombre_Completo"])
            filas.append([Paragraph(fecha, celda), Paragraph(num_es(r["Horometro"]), celda),
                          Paragraph(num_es(r.get("Horas_Trabajadas")), celda), Paragraph(num_es(r["Kilometraje"]), celda),
                          Paragraph(estado, celda), Paragraph(persona, celda)])
        historia.append(tabla(filas, [0.12, 0.13, 0.07, 0.12, 0.39, 0.17]))
        if d["truncado"]:
            historia.append(Paragraph(f"Se muestran los {len(d['bitacora'])} registros más recientes del período.", nota))
    else:
        historia.append(Paragraph("Sin registros en el período seleccionado.", base))
    # ---- Mantenciones
    historia.append(Paragraph("4. Historial de mantenciones", h2))
    historia.append(Paragraph(_esc(d["periodo_texto"]), nota))
    historia.append(Spacer(1, 3))
    if d["mantenciones"]:
        filas = [[Paragraph(t, celda_h) for t in ("Fecha", "Lectura al ingreso", "Trabajo realizado", "Registrado por")]]
        for r in d["mantenciones"]:
            filas.append([Paragraph(_fecha(r["Fecha_Mantencion"]), celda), Paragraph(num_es(r["Horometro_Mecanico"]), celda),
                          Paragraph(_texto_largo(r["Detalle_Trabajo"]), celda), Paragraph(_esc(r["Nombre_Completo"]), celda)])
        historia.append(tabla(filas, [0.12, 0.14, 0.54, 0.20]))
        if d["mantenciones_truncadas"]:
            historia.append(Paragraph(f"Se muestran las {len(d['mantenciones'])} mantenciones más recientes del período.", nota))
    else:
        historia.append(Paragraph("Sin mantenciones registradas en el período seleccionado.", base))
    historia += [Spacer(1, 8), Paragraph("La bitácora operativa es un registro inalterable: los datos no pueden modificarse "
                                         "una vez guardados.", nota)]

    pie = f"Ficha técnica · {m['Patente']} · {d['empresa']}"
    doc.build(historia, canvasmaker=lambda *args, **kw: _LienzoNumerado(*args, pie=pie, **kw))
    return buf.getvalue()
