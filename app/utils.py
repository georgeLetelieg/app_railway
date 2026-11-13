import re
from datetime import datetime
from decimal import Decimal

from flask import current_app


def ahora():
    """Hora actual de Chile, sin zona (convención para columnas DATETIME)."""
    return datetime.now(current_app.config["ZONA_HORARIA"]).replace(tzinfo=None)


def num_es(valor, decimales=2):
    """Formato chileno: 1.234,50"""
    if valor is None:
        return "—"
    texto = f"{float(valor):,.{decimales}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def clp(valor):
    """Pesos chilenos sin decimales: $1.234.567"""
    return "—" if valor is None else "$" + num_es(round(float(valor)), 0)


def parse_numero_es(texto):
    """Convierte un número escrito en formato chileno a Decimal (puede lanzar InvalidOperation).

    Acepta: 85.000 · 1.500 · 120,5 · 1.234,56 · $85000 · 12.5
    Un punto seguido de exactamente 3 dígitos se entiende como separador de miles.
    """
    t = (texto or "").strip().replace("$", "").replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
        t = t.replace(".", "")
    return Decimal(t)
