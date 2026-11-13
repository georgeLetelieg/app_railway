-- =====================================================================
-- gestion_flota - Propuesta 3 (Flask + MySQL)  |  Versión 2.2 (auditoría)
-- Requiere MySQL 8.0.16+ (para que los CHECK se apliquen)
-- =====================================================================
CREATE DATABASE IF NOT EXISTS gestion_flota
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE gestion_flota;

-- 1. rol
CREATE TABLE rol (
    ID_Rol INT AUTO_INCREMENT PRIMARY KEY,
    Nombre_Rol VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB;

-- 2. usuario
CREATE TABLE usuario (
    ID_Usuario INT AUTO_INCREMENT PRIMARY KEY,
    Nombre_Completo VARCHAR(150) NOT NULL,
    Correo VARCHAR(150) NOT NULL UNIQUE,
    Contrasena_Hash VARCHAR(255) NOT NULL,
    Estado_Activo BOOLEAN NOT NULL DEFAULT TRUE,
    Intentos_Fallidos INT NOT NULL DEFAULT 0,
    Fecha_Hora_Bloqueo DATETIME NULL,
    FK_ID_Rol INT NOT NULL,
    FOREIGN KEY (FK_ID_Rol) REFERENCES rol(ID_Rol)
) ENGINE=InnoDB;

-- 3. maquinaria  (Año -> Anio para evitar problemas de codificación)
CREATE TABLE maquinaria (
    ID_Maquinaria INT AUTO_INCREMENT PRIMARY KEY,
    Patente VARCHAR(20) NOT NULL UNIQUE,
    Modelo VARCHAR(100) NOT NULL,
    Anio SMALLINT NOT NULL CHECK (Anio BETWEEN 1900 AND 2100),
    Tipo_Combustible VARCHAR(50) NOT NULL,
    Numero_Chasis VARCHAR(100) NOT NULL UNIQUE,
    Fotografia_Principal VARCHAR(255) NULL,
    Estado_Operativo_Actual ENUM('Operativa','En taller/Mantención','Inactiva')
        NOT NULL DEFAULT 'Operativa',
    Semaforo_Alertas ENUM('Verde','Amarillo','Rojo') NOT NULL DEFAULT 'Verde'
) ENGINE=InnoDB;

-- 4. bitacora_operativa
CREATE TABLE bitacora_operativa (
    ID_Bitacora INT AUTO_INCREMENT PRIMARY KEY,
    FK_ID_Maquinaria INT NOT NULL,
    FK_ID_Usuario INT NOT NULL,
    Fecha_Registro DATE NOT NULL,
    Fecha_Hora_Ingreso DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    Horometro DECIMAL(10,2) NULL CHECK (Horometro IS NULL OR Horometro >= 0),
    Kilometraje DECIMAL(10,2) NULL CHECK (Kilometraje IS NULL OR Kilometraje >= 0),
    Estado_Diario VARCHAR(255) NOT NULL,
    UNIQUE KEY uq_bitacora_maq_fecha (FK_ID_Maquinaria, Fecha_Registro),
    FOREIGN KEY (FK_ID_Maquinaria) REFERENCES maquinaria(ID_Maquinaria),
    FOREIGN KEY (FK_ID_Usuario) REFERENCES usuario(ID_Usuario)
) ENGINE=InnoDB;

-- 5. categoria_documento (reemplaza el ENUM: se pueden agregar categorías sin ALTER)
CREATE TABLE categoria_documento (
    ID_Categoria INT AUTO_INCREMENT PRIMARY KEY,
    Nombre_Categoria VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB;

-- 6. documento
CREATE TABLE documento (
    ID_Documento INT AUTO_INCREMENT PRIMARY KEY,
    FK_ID_Maquinaria INT NOT NULL,
    FK_ID_Usuario INT NOT NULL,
    FK_ID_Categoria INT NOT NULL,
    Ruta_Archivo VARCHAR(255) NOT NULL,
    Nombre_Original VARCHAR(255) NOT NULL,
    Tamano_Archivo_MB DECIMAL(5,2) NOT NULL CHECK (Tamano_Archivo_MB > 0 AND Tamano_Archivo_MB <= 5),
    Fecha_Subida DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (FK_ID_Maquinaria) REFERENCES maquinaria(ID_Maquinaria),
    FOREIGN KEY (FK_ID_Usuario) REFERENCES usuario(ID_Usuario),
    FOREIGN KEY (FK_ID_Categoria) REFERENCES categoria_documento(ID_Categoria)
) ENGINE=InnoDB;

-- 7. carga_combustible
CREATE TABLE carga_combustible (
    ID_Carga INT AUTO_INCREMENT PRIMARY KEY,
    FK_ID_Maquinaria INT NOT NULL,
    FK_ID_Usuario INT NOT NULL,
    Fecha_Carga DATETIME NOT NULL,
    Numero_Vale VARCHAR(50) NULL,
    Litros_Cargados DECIMAL(10,2) NOT NULL CHECK (Litros_Cargados > 0),
    Costo_Total DECIMAL(12,2) NOT NULL CHECK (Costo_Total >= 0),
    INDEX idx_comb_maq_fecha (FK_ID_Maquinaria, Fecha_Carga),
    FOREIGN KEY (FK_ID_Maquinaria) REFERENCES maquinaria(ID_Maquinaria),
    FOREIGN KEY (FK_ID_Usuario) REFERENCES usuario(ID_Usuario)
) ENGINE=InnoDB;

-- 8. alerta_estatica (1 a 1 con maquinaria)
CREATE TABLE alerta_estatica (
    ID_Alerta_Estatica INT AUTO_INCREMENT PRIMARY KEY,
    FK_ID_Maquinaria INT NOT NULL UNIQUE,
    Vencimiento_Revision_Tecnica DATE NULL,
    Vencimiento_Permiso_Circulacion DATE NULL,
    Proxima_Mantencion_Preventiva DECIMAL(10,2) NULL,
    FOREIGN KEY (FK_ID_Maquinaria) REFERENCES maquinaria(ID_Maquinaria)
) ENGINE=InnoDB;

-- 9. alerta_personalizada
CREATE TABLE alerta_personalizada (
    ID_Alerta_Personalizada INT AUTO_INCREMENT PRIMARY KEY,
    FK_ID_Maquinaria INT NOT NULL,
    FK_ID_Usuario INT NOT NULL,
    Titulo_Alerta VARCHAR(255) NOT NULL,
    Fecha_Vencimiento DATE NOT NULL,
    Estado_Activa BOOLEAN NOT NULL DEFAULT TRUE,
    INDEX idx_alerta_pers_venc (Estado_Activa, Fecha_Vencimiento),
    FOREIGN KEY (FK_ID_Maquinaria) REFERENCES maquinaria(ID_Maquinaria),
    FOREIGN KEY (FK_ID_Usuario) REFERENCES usuario(ID_Usuario)
) ENGINE=InnoDB;

-- 10. registro_mantencion
CREATE TABLE registro_mantencion (
    ID_Registro INT AUTO_INCREMENT PRIMARY KEY,
    FK_ID_Maquinaria INT NOT NULL,
    FK_ID_Usuario INT NOT NULL,
    Fecha_Mantencion DATE NOT NULL,
    Horometro_Mecanico DECIMAL(10,2) NOT NULL CHECK (Horometro_Mecanico >= 0),
    Detalle_Trabajo TEXT NOT NULL,
    INDEX idx_mant_maq_fecha (FK_ID_Maquinaria, Fecha_Mantencion),
    FOREIGN KEY (FK_ID_Maquinaria) REFERENCES maquinaria(ID_Maquinaria),
    FOREIGN KEY (FK_ID_Usuario) REFERENCES usuario(ID_Usuario)
) ENGINE=InnoDB;

-- 11. auditoria (registro de correcciones y eliminaciones; solo inserción)
CREATE TABLE auditoria (
    ID_Auditoria INT AUTO_INCREMENT PRIMARY KEY,
    Tabla_Afectada VARCHAR(50) NOT NULL,
    ID_Registro INT NOT NULL,
    FK_ID_Maquinaria INT NULL,
    Accion ENUM('Modificación','Eliminación') NOT NULL,
    FK_ID_Usuario INT NOT NULL,
    Fecha_Hora DATETIME NOT NULL,
    Datos_Anteriores MEDIUMTEXT NOT NULL,
    Datos_Nuevos MEDIUMTEXT NULL,
    INDEX idx_auditoria_tabla_registro (Tabla_Afectada, ID_Registro),
    INDEX idx_auditoria_maquinaria_fecha (FK_ID_Maquinaria, Fecha_Hora),
    FOREIGN KEY (FK_ID_Maquinaria) REFERENCES maquinaria(ID_Maquinaria),
    FOREIGN KEY (FK_ID_Usuario) REFERENCES usuario(ID_Usuario)
) ENGINE=InnoDB;

-- =====================================================================
-- Datos iniciales (sin ellos no se puede crear el primer usuario)
-- =====================================================================
INSERT INTO rol (Nombre_Rol) VALUES
    ('Super Administrador'), ('Administrativo'), ('Gerencia'), ('Contador');

INSERT INTO categoria_documento (Nombre_Categoria) VALUES
    ('Revisión técnica'), ('Factura de compra'),
    ('Permiso de circulación'), ('Mantención correctiva');

-- =====================================================================
-- Bitácora y auditoría inalterables: impiden UPDATE y DELETE a nivel de base de datos
-- (si lo ejecutas desde un cliente que no soporta DELIMITER, omite esta parte)
-- =====================================================================
DELIMITER $$
CREATE TRIGGER trg_bitacora_no_update BEFORE UPDATE ON bitacora_operativa
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'La bitácora operativa es inalterable: no se permite modificar registros.';
END$$

CREATE TRIGGER trg_bitacora_no_delete BEFORE DELETE ON bitacora_operativa
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'La bitácora operativa es inalterable: no se permite eliminar registros.';
END$$

CREATE TRIGGER trg_auditoria_no_update BEFORE UPDATE ON auditoria
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'La auditoría es inalterable: no se permite modificar registros.';
END$$

CREATE TRIGGER trg_auditoria_no_delete BEFORE DELETE ON auditoria
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000'
        SET MESSAGE_TEXT = 'La auditoría es inalterable: no se permite eliminar registros.';
END$$
DELIMITER ;
