"""Gestión de Flota - Áridos y Maquinaria Fuentealba (patrón application factory)."""
import pymysql
from flask import Flask, session

from app.extensions import csrf
from config import Config


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)
    if not app.config.get("SECRET_KEY"):
                raise RuntimeError("Falta la variable de entorno SECRET_KEY (ver .env.example).")

    if app.config.get("DETRAS_DE_PROXY"):
        # Railway termina el HTTPS: confiar en X-Forwarded-For/Proto de un único proxy
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    csrf.init_app(app)

    # Blueprints (un módulo por funcionalidad)
    from app.auth.routes import bp as auth_bp
    from app.main.routes import bp as main_bp
    app.register_blueprint(auth_bp)
    from app.maquinaria.routes import bp as maquinaria_bp
    from app.bitacora.routes import bp as bitacora_bp
    from app.documentos.routes import bp as documentos_bp
    from app.combustible.routes import bp as combustible_bp
    from app.alertas.routes import bp as alertas_bp
    from app.alertas.personalizadas import bp as personalizadas_bp
    from app.reportes.routes import bp as reportes_bp
    from app.mantenciones.routes import bp as mantenciones_bp
    from app.auditoria.routes import bp as auditoria_bp
    from app.usuarios.routes import bp as usuarios_bp
    from app.alertas import motor
    from app.utils import clp, num_es
    app.register_blueprint(main_bp)
    app.register_blueprint(maquinaria_bp)
    app.register_blueprint(bitacora_bp)
    app.register_blueprint(documentos_bp)
    app.register_blueprint(combustible_bp)
    app.register_blueprint(alertas_bp)
    app.register_blueprint(personalizadas_bp)
    app.register_blueprint(reportes_bp)
    app.register_blueprint(mantenciones_bp)
    app.register_blueprint(auditoria_bp)
    app.register_blueprint(usuarios_bp)
    app.jinja_env.filters.update(clp=clp, num_es=num_es)

    @app.context_processor
    def permisos():
        rol = session.get("rol")
        return {"puede_escribir": rol in app.config["ROLES_ESCRITURA"],
                "puede_corregir_base": rol in app.config["ROLES_CORRECCION_BASE"],
                "puede_ver_auditoria": rol in app.config["ROLES_AUDITORIA"],
                # Solo para mostrar u ocultar el enlace del menú; la protección real es @admin_required
                "es_admin": rol == app.config["ROL_SUPER_ADMIN"]}

    @app.context_processor
    def avisos_en_el_menu():
        def resumen_alertas():
            return motor.resumen(motor.flota_alertas()) if "usuario_id" in session else None
        return {"resumen_alertas": resumen_alertas}

    @app.errorhandler(413)
    def archivo_demasiado_grande(error):
        return "El archivo supera el límite de 5 MB.", 413

    from app.cli import crear_usuario
    app.cli.add_command(crear_usuario)

    @app.errorhandler(pymysql.MySQLError)
    def error_base_de_datos(error):
        app.logger.exception("Error de base de datos: %s", error)
        return "El servicio no está disponible en este momento. Intenta nuevamente.", 503

    return app
