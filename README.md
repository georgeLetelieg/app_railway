# Gestión de Flota – Áridos y Maquinaria Fuentealba

Plataforma web (Propuesta Técnica 3): Python 3.12 · Flask 3 · MySQL 8.0.16+ · Bootstrap 5.
Proyecto Integrador A+S – C.F.T. del Maule.

## Estructura

```
flota_app/
├── run.py                  Punto de entrada
├── config.py               Configuración (lee el archivo .env)
├── requirements.txt        Dependencias de la aplicación
├── requirements-dev.txt    Dependencias + pytest
├── .env.example            Plantilla de variables de entorno
├── database/
│   └── gestion_flota_v2.sql    Script de la base de datos
├── docs/
│   └── bd_proyecto_sc_v2.docx  Documentación de la base de datos
├── app/
│   ├── __init__.py         create_app() y registro de módulos
│   ├── db.py               Conexión a MySQL
│   ├── auditoria/          Auditoría de correcciones y eliminaciones (consulta)
│   ├── usuarios/           Gestión de usuarios: listar, crear, activar/inactivar/desbloquear [listo]
│   ├── security.py         @login_required y @roles_required
│   ├── archivos.py         Validación y guardado de archivos subidos
│   ├── utils.py            Utilidades (hora de Chile, etc.)
│   ├── cli.py              Comando crear-usuario
│   ├── extensions.py       Protección CSRF
│   ├── auth/               Login y logout                  [listo]
│   ├── main/               Dashboard con resumen de la flota [listo]
│   ├── maquinaria/         Perfiles de flota (RF1)         [listo]
│   ├── bitacora/           Bitácora operativa (RF2)        [listo]
│   ├── documentos/         Gestor documental (RF3)         [listo]
│   ├── combustible/        Combustible (RF4)               [listo]
│   ├── alertas/            Alertas estáticas (RF5) y personalizadas (RF6) [listo]
│   ├── mantenciones/       Historial de mantenciones (RF8) [listo]
│   ├── reportes/           Ficha técnica en PDF (RF7)      [listo]
│   ├── templates/          base.html + una carpeta por módulo
│   └── static/             css/, js/, img/
├── uploads/                Archivos subidos (fuera de static; no van a Git)
│   ├── fotos/
│   └── documentos/
└── tests/                  Pruebas automáticas (pytest) e integracion_mysql.py
```

Cada módulo pendiente tendrá su propio `routes.py` (Blueprint), sus consultas y su carpeta en `templates/`.

## Puesta en marcha

```
python -m venv .venv
.venv\Scripts\activate            (Windows)   |   source .venv/bin/activate  (Linux/Mac)
pip install -r requirements-dev.txt
copy .env.example .env            (completa SECRET_KEY y los datos de MySQL)
```

1. Ejecuta `database/gestion_flota_v2.sql` en MySQL.
2. Crea el primer usuario: `flask --app run crear-usuario`
3. Inicia el servidor: `flask --app run run`  →  http://127.0.0.1:5000
4. Pruebas: `pytest`

## Módulo maquinaria (RF1)
- `/maquinaria/` lista con búsqueda (patente/modelo) y filtro por estado operativo.
- `/maquinaria/nueva`, `/maquinaria/<id>` (perfil), `/maquinaria/<id>/editar`, `/maquinaria/<id>/eliminar`.
- Al crear una máquina se crea también su fila en `alerta_estatica` (relación 1 a 1).
- Datos base inmutables: al editar solo se cambian el estado operativo y la fotografía.
- La fotografía se valida con Pillow, se reduce a 1600 px y se guarda como JPG en `uploads/fotos/`.
- Eliminar solo es posible si la máquina no tiene historial; si lo tiene, se marca como «Inactiva».
- Roles con escritura: `ROLES_ESCRITURA` en `config.py` (los demás solo consultan).

## Módulo bitácora (RF2)
- `/maquinaria/<id>/bitacora/` historial con filtro por fechas, paginación y últimas lecturas.
- `/maquinaria/<id>/bitacora/nuevo` registro de un día (acepta `?fecha=AAAA-MM-DD`).
- Un registro por máquina y día; las lecturas son opcionales (días sin actividad) y no pueden retroceder.
- Es inalterable: no existen rutas de edición ni eliminación, y los triggers del script SQL lo impiden en la base.
- Avisa los días sin registro de los últimos 30 días (desde el primer registro), con enlace para completarlos.

## Módulo documentos (RF3)
- `/maquinaria/<id>/documentos/` lista con filtro por categoría; `subir`, `<doc>/ver`, `<doc>/descargar`, `<doc>/editar`, `<doc>/eliminar`.
- Acepta PDF, JPG, PNG y WEBP de hasta 5 MB. El tipo se detecta por el contenido real, no por la extensión.
- Se guardan en `uploads/documentos/` con nombre aleatorio; en la base quedan la ruta, el nombre original (saneado) y el tamaño.
- Editar permite cambiar categoría y nombre, y reemplazar el archivo (el anterior se borra del disco).
- Las categorías viven en la tabla `categoria_documento` (se agregan con un INSERT, sin tocar el código).

## Módulo combustible (RF4)
- `/combustible/` apartado general: cuadratura por maquinaria (cargas, litros, costo, costo por litro), totales y detalle paginado.
- Período por defecto: mes en curso. Filtros por maquinaria y rango de fechas; con las fechas vacías se ve todo el historial.
- `/combustible/nueva`, `/combustible/<id>/editar`, `/combustible/<id>/eliminar` (roles de escritura).
- `/combustible/exportar.csv`: exporta lo filtrado (separador `;`, decimales con coma, UTF-8 con BOM) para cruzar con facturas en Excel.
- Acepta números en formato chileno (`120,5`, `125.000`, `$85000`). Tope de 5.000 L por carga para detectar errores de digitación (`LITROS_MAX_CARGA`).
- El N° de vale no puede repetirse dentro de la misma maquinaria.

## Módulo alertas estáticas (RF5)
- Panel en el perfil de cada máquina: revisión técnica, permiso de circulación y próxima mantención preventiva.
- Avisos a 30 días o menos (documentos) y a 50 horas o menos (mantención); se ajustan en `ALERTA_DIAS_DOCUMENTOS` y `ALERTA_HORAS_MANTENCION` (`config.py`).
- La mantención se compara contra la última lectura de horómetro de la bitácora; el valor «próxima mantención» es el horómetro objetivo.
- Semáforo (se calcula al consultar, no se guarda): rojo = algo vencido · amarillo = algo por vencer · gris = datos incompletos · verde = al día. La maquinaria «Inactiva» no genera avisos.
- Dónde se ven los avisos: perfil, lista de flota, panel de inicio, centro de alertas (`/alertas/`) y contador en el menú.
- Los contadores del centro de alertas cuentan **alertas** (vencidas, por vencer, sin registrar y vigentes) y coinciden con las filas de su tabla y con el número del menú (vencidas + por vencer). Debajo se muestra el resumen por maquinaria (semáforo).
- Editar vencimientos: `/maquinaria/<id>/alertas/editar` (roles de escritura).

## Módulo alertas personalizadas (RF6)
- Desde el perfil de la máquina: `+ Nueva alerta` con título (3 a 255 caracteres) y fecha de vencimiento.
- Se notifican desde `ALERTA_DIAS_PERSONALIZADAS` días antes (por defecto 7; con 0 avisan solo el día del vencimiento) y pasan a «vencida» al día siguiente.
- Alimentan el semáforo, el centro de alertas (`/alertas/`), el panel de inicio y el contador del menú, igual que las estáticas.
- Acciones: editar, marcar como cumplida (deja de notificar pero queda en el historial), reactivar y eliminar (roles de escritura).
- No se permiten dos alertas con el mismo título y fecha en la misma máquina.

## Módulo reportes (RF7)
- `/maquinaria/<id>/ficha.pdf` genera la ficha técnica en PDF (tamaño Carta): fotografía, datos base, últimas lecturas, alertas y vencimientos (estáticas y personalizadas) e historial operativo.
- `?periodo=30|90|180|365|todo` define cuánta bitácora se incluye (por defecto 90 días; tope de 1.500 filas, `MAX_FILAS_FICHA`).
- Por defecto se abre en el navegador (para imprimir); con `?descargar=1` se descarga como `ficha_<PATENTE>_<AAAAMMDD>.pdf`.
- Cualquier rol puede exportarla; el PDF indica quién y cuándo la emitió. Pie de página «Página X de Y» y cabecera de tabla repetida.
- Se genera con ReportLab (sin dependencias del sistema). Todo texto de usuario se escapa antes de entrar al PDF.
- El nombre de la empresa sale de `EMPRESA_NOMBRE` en `config.py`.

## Módulo mantenciones (RF8)
- `/maquinaria/<id>/mantenciones/` historial con búsqueda dentro del detalle y paginación; `nueva`, `<id>/editar`, `<id>/eliminar` (roles de escritura).
- Cada registro guarda fecha, lectura exacta de horómetro/kilometraje al ingreso al taller y un texto libre (hasta ≈60.000 caracteres, conserva saltos de línea).
- Las lecturas no pueden retroceder respecto de mantenciones de fechas anteriores/posteriores; se permiten varias el mismo día.
- Al registrar, el campo opcional «próxima mantención en N horas» fija la meta de la alerta de mantención (RF5) = lectura + N, solo si es la mantención más reciente.
- Está separado de la bitácora de uso diario. Aparece en el perfil y en la ficha PDF (sección 4).

## Gestión de usuarios (solo Super Administrador)
- `/usuarios/` lista los usuarios (nombre, correo, rol y estado: **Activo**, **Inactivo** o **Bloqueado**), con resumen, búsqueda y filtro por estado.
- `/usuarios/nuevo` crea un usuario: el rol se elige de un desplegable (sale de la tabla `rol`, sin tipeo libre) y la contraseña se guarda siempre con hash (`METODO_HASH`), nunca en claro.
- Botones por usuario: **Desbloquear** (cuentas bloqueadas por intentos fallidos), **Inactivar** y **Activar**. Nadie puede inactivar su propia cuenta.
- Todas las rutas usan `@admin_required` (`app/security.py`), que valida el rol **contra la base de datos en cada petición**: si a un Super Administrador lo desactivan o le cambian el rol, pierde el acceso de inmediato. El enlace del menú solo se muestra a ese rol.
- Política de contraseñas (web y comando `crear-usuario`): 10 a 128 caracteres, con al menos una letra y un número, y distinta del correo y del nombre.
- Los cambios de estado quedan en la auditoría (`/auditoria/?tabla=usuario`); nunca se guarda la contraseña ni su hash. El primer usuario sigue creándose con `flask --app run crear-usuario`.

## Auditoría y correcciones
- Editar o eliminar cargas de combustible y mantenciones queda registrado en la tabla `auditoria`: quién, cuándo y cómo era el dato antes. Crear un dato no se audita (su autor ya está en el registro).
- El Super Administrador puede corregir los datos base de una maquinaria (patente, modelo, año, combustible, chasis); el cambio también se audita. Se controla con `ROLES_CORRECCION_BASE` en `config.py`.
- `/auditoria/` muestra los registros con filtros por dato, maquinaria y fechas (roles en `ROLES_AUDITORIA`: Super Administrador y Gerencia). Es de solo lectura; en la base, triggers impiden modificar o borrar la auditoría.
- El cambio y su registro se guardan en la misma transacción: se guardan ambos o ninguno.
- Una maquinaria con registros de auditoría no puede eliminarse (se marca «Inactiva»).

## Verificación contra MySQL real
Las pruebas de `pytest` usan una base SQLite simulada (rápida, sin instalar nada). Para comprobar el proyecto contra tu MySQL:

1. Crea una base nueva ejecutando `database/gestion_flota_v2.sql` y completa `.env`.
2. `python tests/integracion_mysql.py` — 154 verificaciones sobre todos los módulos, pantallas, roles, bloqueo de cuentas, CHECK y triggers (se niega a correr si ya hay usuarios).

Verificado con MySQL 8.0.46. Los `CHECK` requieren 8.0.16 o superior; en versiones anteriores MySQL los ignora sin error y la aplicación sigue validando las mismas reglas.

## Convenciones
- Tablas en minúsculas; columnas tal como están en el script SQL.
- Números: se aceptan en formato chileno (`1.250`, `120,5`, `$85.000`) con `app.utils.parse_numero_es()`.
- Fechas: hora de Chile sin zona, con `app.utils.ahora()` (no usar `datetime.utcnow()`).
- Nuevas rutas protegidas: `@login_required` (y `@roles_required(...)` si corresponde).
- Los archivos subidos se guardan en `uploads/` (máx. 5 MB) y en la base solo se guarda la ruta.
