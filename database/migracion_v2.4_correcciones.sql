-- =====================================================================
-- gestion_flota  |  Migración v2.3 -> v2.4: correcciones de seguridad e integridad
-- Ejecutar UNA sola vez sobre una base existente (local y Railway), con un usuario administrador
-- (root en Railway). Requiere haber aplicado antes migracion_v2.3_parte_diario.sql.
-- =====================================================================
USE gestion_flota;

-- ---------------------------------------------------------------------
-- PASO 0 (solo lectura): revisar duplicados que impedirían crear los índices UNIQUE.
-- Si alguna de estas consultas devuelve filas, corrige o elimina esos registros
-- desde la aplicación ANTES de continuar con el paso 2.
-- ---------------------------------------------------------------------
SELECT FK_ID_Maquinaria, Numero_Vale, COUNT(*) AS veces
  FROM carga_combustible
 WHERE Numero_Vale IS NOT NULL
 GROUP BY FK_ID_Maquinaria, Numero_Vale HAVING COUNT(*) > 1;

SELECT FK_ID_Maquinaria, Titulo_Alerta, Fecha_Vencimiento, COUNT(*) AS veces
  FROM alerta_personalizada
 GROUP BY FK_ID_Maquinaria, Titulo_Alerta, Fecha_Vencimiento HAVING COUNT(*) > 1;

-- ---------------------------------------------------------------------
-- PASO 1: límite de intentos de inicio de sesión por IP
-- ---------------------------------------------------------------------
CREATE TABLE intento_login (
    ID_Intento BIGINT AUTO_INCREMENT PRIMARY KEY,
    IP VARCHAR(45) NOT NULL,
    Fecha_Hora DATETIME NOT NULL,
    INDEX idx_intento_ip_fecha (IP, Fecha_Hora),
    INDEX idx_intento_fecha (Fecha_Hora)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------
-- PASO 2: impedir duplicados por envíos simultáneos
-- ---------------------------------------------------------------------
ALTER TABLE carga_combustible
    ADD UNIQUE KEY uq_comb_maq_vale (FK_ID_Maquinaria, Numero_Vale);

ALTER TABLE alerta_personalizada
    ADD UNIQUE KEY uq_alerta_pers (FK_ID_Maquinaria, Titulo_Alerta, Fecha_Vencimiento);

-- ---------------------------------------------------------------------
-- PASO 3: recordar qué mantención programó la próxima mantención preventiva
-- (las metas ya existentes quedan sin vínculo: se vinculan al programar la siguiente)
-- ---------------------------------------------------------------------
ALTER TABLE alerta_estatica
    ADD COLUMN FK_ID_Mantencion_Origen INT NULL AFTER Proxima_Mantencion_Preventiva,
    ADD COLUMN Intervalo_Mantencion DECIMAL(10,2) NULL AFTER FK_ID_Mantencion_Origen,
    ADD CONSTRAINT fk_alerta_mant_origen FOREIGN KEY (FK_ID_Mantencion_Origen)
        REFERENCES registro_mantencion(ID_Registro) ON DELETE SET NULL;

-- ---------------------------------------------------------------------
-- PASO 4: eliminar la columna sin uso (el semáforo se calcula al consultar)
-- ---------------------------------------------------------------------
ALTER TABLE maquinaria DROP COLUMN Semaforo_Alertas;
