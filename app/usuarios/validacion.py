"""Validaciones compartidas por la gestión web de usuarios y el comando de consola `crear-usuario`."""
import re

PASSWORD_MIN = 10
PASSWORD_MAX = 128
NOMBRE_MIN, NOMBRE_MAX = 3, 150
CORREO_MAX = 150
_PATRON_CORREO = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

POLITICA_CONTRASENA = f"Mínimo {PASSWORD_MIN} caracteres, con al menos una letra y un número."


def normalizar_nombre(texto):
    """Recorta y colapsa los espacios repetidos."""
    return " ".join((texto or "").split())


def normalizar_correo(texto):
    return (texto or "").strip().lower()


def validar_nombre(nombre):
    if not NOMBRE_MIN <= len(nombre) <= NOMBRE_MAX:
        return f"El nombre es obligatorio ({NOMBRE_MIN} a {NOMBRE_MAX} caracteres)."
    return None


def validar_correo(correo):
    if not correo or len(correo) > CORREO_MAX or not _PATRON_CORREO.match(correo):
        return "Ingresa un correo electrónico válido."
    return None


def validar_contrasena(password, correo="", nombre=""):
    """Devuelve la lista de problemas de la contraseña (vacía si es aceptable)."""
    errores = []
    password = password or ""
    if len(password) < PASSWORD_MIN:
        errores.append(f"La contraseña debe tener al menos {PASSWORD_MIN} caracteres.")
    if len(password) > PASSWORD_MAX:
        errores.append(f"La contraseña no puede superar los {PASSWORD_MAX} caracteres.")
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        errores.append("La contraseña debe incluir al menos una letra y un número.")
    if correo and password.lower() in {correo.lower(), correo.split("@")[0].lower()}:
        errores.append("La contraseña no puede ser igual al correo.")
    if nombre and password.lower() == nombre.lower():
        errores.append("La contraseña no puede ser igual al nombre.")
    return errores
