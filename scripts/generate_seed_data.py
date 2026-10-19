from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
BASE_CREATED_AT = datetime(2026, 2, 3, 7, 30, tzinfo=UTC)


def iso(days: int, hours: int = 0) -> str:
    return (BASE_CREATED_AT + timedelta(days=days, hours=hours)).isoformat()


def slug(value: str) -> str:
    table = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return value.translate(table).lower().replace(" ", "-").replace("/", "-").replace(",", "")


def build_document_content(
    *,
    title: str,
    system: str,
    department: str,
    objective: str,
    scope: str,
    steps: list[str],
    controls: list[str],
    evidence: str,
    escalation: str,
) -> str:
    steps_text = " ".join(f"{index}. {step}." for index, step in enumerate(steps, start=1))
    controls_text = " ".join(f"- {control}." for control in controls)
    return "\n\n".join(
        [
            (
                f"Objetivo. {title} define un criterio operativo para {department} dentro de {system}. "
                f"{objective} El documento está redactado para equipos internos de una compañía logística ficticia "
                "y usa ejemplos originales de almacén, transporte, soporte y control de calidad."
            ),
            (
                f"Alcance. {scope} Aplica a coordinadores de turno, backoffice operativo, supervisores de almacén, "
                "seguridad o soporte funcional según el proceso afectado. Si el caso no coincide con este alcance, "
                "debe pedirse aclaración antes de ejecutar cambios."
            ),
            (
                f"Pasos de trabajo. {steps_text} El operador debe registrar cada decisión en el expediente operativo, "
                "indicando centro, turno, referencia de pedido, ruta, lote o documento cuando corresponda."
            ),
            (
                f"Controles mínimos. {controls_text} Ningún cierre se considera válido si queda una diferencia visible "
                "entre panel operativo, historial del sistema y evidencia revisada por el responsable del turno."
            ),
            (
                f"Evidencia esperada. {evidence} La evidencia debe permitir reconstruir qué se revisó, qué dato cambió "
                "y quién aprobó la acción. Las notas vagas o sin referencia operativa no son suficientes para cerrar el caso."
            ),
            (
                f"Escalado. {escalation} Cuando el asistente interno use este documento como fuente, debe citar el sistema, "
                "el procedimiento y los pasos relevantes, sin convertir incidencias parecidas en una solución definitiva si "
                "falta contexto del usuario."
            ),
        ]
    )


DOCUMENT_BLUEPRINTS = [
    {
        "id": 1,
        "title": "Cierre de expediciones en AlmaTrack WMS",
        "document_type": "procedimiento",
        "department": "Operaciones",
        "affected_system": "AlmaTrack WMS",
        "objective": "Explica cómo cerrar expediciones cuando el almacén ya confirmó preparación, embalaje y salida física.",
        "scope": "Cubre expediciones estándar, parciales y cierres con bultos retenidos por ubicación o muelle.",
        "steps": [
            "Validar que todas las tareas de picking y packing estén cerradas",
            "Comprobar bultos esperados contra bultos leídos en muelle",
            "Registrar excepción si queda mercancía retenida",
            "Confirmar expedición y revisar que el estado llegue al panel de seguimiento",
        ],
        "controls": [
            "No cerrar si existen tareas RF abiertas",
            "No modificar cantidades sin conteo de supervisor",
            "Revisar etiquetas de transporte antes de liberar el muelle",
        ],
        "evidence": "captura interna del cierre, lista de bultos, muelle, usuario de confirmación y observación de cualquier entrega parcial.",
        "escalation": "Si el cierre no propaga estado en diez minutos, abrir ticket en HelpOps con prioridad alta y bloquear nuevas salidas del pedido afectado.",
    },
    {
        "id": 2,
        "title": "Gestión de diferencias de stock y conteos cíclicos",
        "document_type": "procedimiento",
        "department": "Operaciones",
        "affected_system": "AlmaTrack WMS",
        "objective": "Describe cómo aislar una diferencia de stock sin contaminar el inventario disponible ni frenar zonas no afectadas.",
        "scope": "Aplica a conteos cíclicos, ubicaciones de picking, reserva de expedición y mercancía en cuarentena operativa.",
        "steps": [
            "Poner la ubicación en revisión",
            "Contar físico, reservado y en tránsito por separado",
            "Comparar el histórico de movimientos",
            "Cerrar ajuste solo con aprobación del supervisor",
        ],
        "controls": [
            "No liberar picking si el stock físico no cuadra",
            "Distinguir merma, reubicación y reserva pendiente",
            "Documentar si la diferencia afecta a lote o caducidad",
        ],
        "evidence": "recuento firmado, motivo de ajuste, ubicación, lote, artículo y comparación antes y después.",
        "escalation": "Si la diferencia se repite en dos conteos consecutivos, escalar a reconciliación ERP-WMS.",
    },
    {
        "id": 3,
        "title": "Reasignación de rutas y transportistas en RutaNexo TMS",
        "document_type": "procedimiento",
        "department": "Operaciones",
        "affected_system": "RutaNexo TMS",
        "objective": "Establece el flujo para cambiar ruta, transportista o secuencia de paradas manteniendo promesa de entrega.",
        "scope": "Cubre cierres de carretera, ausencia de conductor, cambio de vehículo, parada prioritaria y reparto de carga entre rutas.",
        "steps": [
            "Congelar la versión activa de la ruta",
            "Revisar ventanas, restricciones de vehículo y observaciones de cliente",
            "Seleccionar transportista alternativo validado",
            "Publicar nueva secuencia y confirmar recepción por el conductor",
        ],
        "controls": [
            "No recalcular sin revisar paradas críticas",
            "Conservar motivo de cambio",
            "Revisar impacto en POD y CMR",
        ],
        "evidence": "versión de ruta anterior y nueva, transportista, paradas afectadas, ETA revisado y usuario que publica.",
        "escalation": "Si la ruta queda congelada o sin secuencia final, HelpOps debe recibir ticket crítico con identificador de ruta y centro.",
    },
    {
        "id": 4,
        "title": "Gestión de pedidos retenidos en LogiCore ERP",
        "document_type": "guía de diagnóstico",
        "department": "Operaciones",
        "affected_system": "LogiCore ERP",
        "objective": "Ayuda a revisar pedidos bloqueados por reglas de negocio, datos maestros o incoherencias de preparación.",
        "scope": "Aplica a pedidos de cliente, intercentro y reposición que no avanzan a almacén o facturación interna.",
        "steps": [
            "Identificar regla de retención",
            "Verificar dirección, condiciones de entrega y líneas duplicadas",
            "Contrastar disponibilidad con AlmaTrack WMS",
            "Liberar, rechazar o devolver a backoffice con motivo",
        ],
        "controls": [
            "No liberar si falta validación de datos maestros",
            "No forzar estado si WMS no tiene stock disponible",
            "Mantener observación de auditoría",
        ],
        "evidence": "captura del bloqueo, regla aplicada, usuario revisor, pedido, centro y decisión final.",
        "escalation": "Si el pedido combina error de stock y retención manual, priorizar reconciliación antes de prometer salida.",
    },
    {
        "id": 5,
        "title": "Alta y baja de usuarios operativos en SafeGate",
        "document_type": "procedimiento de seguridad",
        "department": "Seguridad",
        "affected_system": "SafeGate",
        "objective": "Define altas, bajas y cambios de acceso para usuarios internos, visitas técnicas y personal externo de almacén.",
        "scope": "Aplica a accesos físicos, grupos de zona, horarios de turno y revocaciones al finalizar actividad.",
        "steps": [
            "Comprobar rol operativo y responsable interno",
            "Asignar zona, horario y duración",
            "Validar entrada de prueba si es alta crítica",
            "Revocar permiso al terminar relación o servicio",
        ],
        "controls": [
            "No reutilizar credenciales de otra persona",
            "Evitar permisos permanentes para visitas",
            "Registrar aprobador y fecha de caducidad",
        ],
        "evidence": "solicitud aprobada, grupo asignado, zona, caducidad y confirmación de baja cuando aplique.",
        "escalation": "Cualquier acceso denegado repetido debe tratarse como incidencia de seguridad y no como simple duda operativa.",
    },
    {
        "id": 6,
        "title": "Escalado de incidencias críticas en HelpOps",
        "document_type": "guía de escalado",
        "department": "Seguridad",
        "affected_system": "HelpOps",
        "objective": "Clasifica cuándo una incidencia debe elevarse por impacto en expedición, seguridad, calidad o continuidad de turno.",
        "scope": "Aplica a bloqueos con pedidos parados, rutas congeladas, accesos masivos denegados, lotes bloqueados y pérdida de trazabilidad.",
        "steps": [
            "Registrar síntoma observable",
            "Indicar sistema, centro, turno y referencia",
            "Asignar prioridad según impacto",
            "Notificar al responsable de guardia si hay parada operativa",
        ],
        "controls": [
            "No cerrar sin evidencia de recuperación",
            "No duplicar tickets sin relacionarlos",
            "Mantener estado abierto si no hay resolución validada",
        ],
        "evidence": "ticket con categoría correcta, impacto, referencias operativas y relación con documentos o incidencias previas.",
        "escalation": "Si hay riesgo de seguridad o calidad, el escalado debe ser inmediato aunque el volumen afectado sea bajo.",
    },
    {
        "id": 7,
        "title": "Onboarding de coordinadores de turno en OnboardHub",
        "document_type": "guía de onboarding",
        "department": "Onboarding",
        "affected_system": "OnboardHub",
        "objective": "Enumera los pasos mínimos para que una persona nueva pueda operar como coordinador sin depender de conocimiento informal.",
        "scope": "Cubre formación inicial, lectura de procedimientos, permisos, acompañamiento y validación de primer turno.",
        "steps": [
            "Completar ficha de rol y centro",
            "Leer procedimientos críticos en DocuFlow",
            "Asignar mentor y turno de sombra",
            "Validar permisos de LogiCore ERP, AlmaTrack WMS, RutaNexo TMS y SafeGate",
        ],
        "controls": [
            "No cerrar onboarding sin mentor asignado",
            "Verificar acuse de lectura",
            "Confirmar que los permisos coinciden con el rol real",
        ],
        "evidence": "checklist firmado, mentor, módulos completados, permisos asignados y fecha de validación.",
        "escalation": "Si falta acuse de lectura o permiso esencial, Onboarding mantiene el expediente abierto y abre ticket en HelpOps.",
    },
    {
        "id": 8,
        "title": "Extracción y validación de documentos en ScanBridge IDP",
        "document_type": "procedimiento",
        "department": "Operaciones",
        "affected_system": "ScanBridge IDP",
        "objective": "Describe cómo validar OCR de albaranes, POD, CMR y documentos de recepción antes de integrarlos en el expediente.",
        "scope": "Aplica a documentos escaneados, anexos ilegibles, campos de pedido, matrícula, transportista, fecha y firma.",
        "steps": [
            "Revisar calidad de imagen",
            "Comparar campos extraídos contra pedido o ruta",
            "Marcar campos dudosos para revisión manual",
            "Enviar solo documentos validados a DocuFlow o LogiCore ERP",
        ],
        "controls": [
            "No aceptar matrícula o firma con baja confianza",
            "No mezclar documentos de rutas distintas",
            "Guardar motivo de corrección manual",
        ],
        "evidence": "imagen revisada, campos corregidos, motivo de validación manual y referencia documental.",
        "escalation": "Si dos extracciones fallan para el mismo tipo de documento, crear incidencia de análisis en HelpOps.",
    },
    {
        "id": 9,
        "title": "Gestión de POD, CMR y justificantes de entrega",
        "document_type": "procedimiento",
        "department": "Operaciones",
        "affected_system": "RutaNexo TMS",
        "objective": "Define cómo recoger, validar y asociar pruebas de entrega y documentos de transporte al cierre de ruta.",
        "scope": "Aplica a POD digital, CMR escaneado, justificantes con firma parcial, entrega fallida y devolución de bultos.",
        "steps": [
            "Comprobar que el documento pertenece a la parada correcta",
            "Validar fecha, firma y bultos entregados",
            "Asociar justificante a ruta y pedido",
            "Registrar excepción si falta prueba o hay entrega parcial",
        ],
        "controls": [
            "No cerrar ruta sin motivo si falta POD",
            "Distinguir entrega fallida de entrega parcial",
            "Conservar documento original escaneado",
        ],
        "evidence": "identificador de ruta, parada, pedido, justificante y observación de excepción.",
        "escalation": "Si el justificante no es legible, usar ScanBridge IDP y dejar el expediente en revisión.",
    },
    {
        "id": 10,
        "title": "Bloqueo y desbloqueo de lotes por calidad en QualiTrace QMS",
        "document_type": "procedimiento de calidad",
        "department": "Seguridad",
        "affected_system": "QualiTrace QMS",
        "objective": "Establece cuándo bloquear lotes, cómo liberar producto y qué evidencias necesita Calidad para cerrar una no conformidad.",
        "scope": "Aplica a lote dañado, temperatura fuera de rango, embalaje comprometido, reclamación de cliente o auditoría interna.",
        "steps": [
            "Bloquear lote y ubicaciones asociadas",
            "Registrar motivo y alcance",
            "Tomar muestra o evidencia visual",
            "Liberar solo tras dictamen de Calidad",
        ],
        "controls": [
            "No mover lote bloqueado a expedición",
            "No cerrar sin dictamen",
            "Informar a LogiCore ERP si afecta pedidos retenidos",
        ],
        "evidence": "lote, artículo, ubicación, dictamen de calidad, fotos internas y decisión de liberación o destrucción.",
        "escalation": "Si el lote afecta pedidos ya preparados, Operaciones debe coordinar sustitución antes de cerrar expedición.",
    },
    {
        "id": 11,
        "title": "Reconciliación ERP-WMS ante diferencias de stock",
        "document_type": "guía de diagnóstico",
        "department": "Operaciones",
        "affected_system": "LogiCore ERP",
        "objective": "Guía para comparar stock administrativo y stock operativo cuando ERP y WMS muestran cantidades distintas.",
        "scope": "Aplica a pedidos bloqueados, reposición, ubicaciones en cuarentena y ajustes posteriores a recepción.",
        "steps": [
            "Identificar artículo, lote y centro",
            "Comparar disponible, reservado y en tránsito",
            "Revisar último movimiento en ambos sistemas",
            "Corregir origen de la diferencia antes de ajustar destino",
        ],
        "controls": [
            "No ajustar ambos sistemas a la vez",
            "Evitar liberar pedidos sin stock confirmado",
            "Registrar sistema origen del descuadre",
        ],
        "evidence": "captura comparativa, movimiento causante, responsable de ajuste y validación cruzada posterior.",
        "escalation": "Si no se identifica origen en el turno, mantener bloqueo y escalar a soporte funcional.",
    },
    {
        "id": 12,
        "title": "Procedimiento de recepción de mercancía",
        "document_type": "procedimiento",
        "department": "Operaciones",
        "affected_system": "AlmaTrack WMS",
        "objective": "Detalla la recepción física y documental de mercancía entrante antes de poner stock disponible para preparación.",
        "scope": "Aplica a proveedores, traspasos intercentro, devoluciones de cliente y mercancía con documentación parcial.",
        "steps": [
            "Registrar llegada y muelle",
            "Validar pedido o aviso de entrada",
            "Contar bultos y revisar daños",
            "Ubicar mercancía o dejarla en revisión",
        ],
        "controls": [
            "No recibir sin referencia operativa",
            "Separar discrepancias de cantidad y calidad",
            "Informar a ScanBridge IDP si la documentación es ilegible",
        ],
        "evidence": "aviso de entrada, bultos recibidos, diferencias, fotos internas y ubicación final.",
        "escalation": "Si hay daño o lote dudoso, activar QualiTrace QMS antes de liberar stock.",
    },
    {
        "id": 13,
        "title": "Gestión de devoluciones de cliente",
        "document_type": "guía",
        "department": "Seguridad",
        "affected_system": "LogiCore ERP",
        "objective": "Explica cómo registrar devoluciones, clasificar motivo y decidir si el producto vuelve a stock, revisión o calidad.",
        "scope": "Aplica a devolución por daño, error de preparación, entrega rechazada, producto equivocado y documentación incompleta.",
        "steps": [
            "Crear expediente de devolución",
            "Asociar pedido y motivo",
            "Enviar mercancía a ubicación de revisión",
            "Actualizar estado tras dictamen operativo o de calidad",
        ],
        "controls": [
            "No reincorporar producto sin revisión",
            "Mantener trazabilidad de bultos",
            "Distinguir devolución comercial de incidencia logística",
        ],
        "evidence": "pedido, motivo, bultos, estado físico, decisión y sistema donde se actualiza.",
        "escalation": "Si la devolución contiene daño o sospecha de lote, Calidad debe revisar en QualiTrace QMS.",
    },
    {
        "id": 14,
        "title": "Política de permisos temporales para personal externo",
        "document_type": "política",
        "department": "Seguridad",
        "affected_system": "SafeGate",
        "objective": "Define límites para permisos de proveedores, auditorías, visitas técnicas y personal temporal.",
        "scope": "Aplica a accesos físicos por centro, zonas restringidas, horarios excepcionales y acompañamiento obligatorio.",
        "steps": [
            "Recoger responsable interno",
            "Asignar alcance mínimo necesario",
            "Definir caducidad",
            "Revisar cierre del permiso",
        ],
        "controls": [
            "No crear accesos indefinidos",
            "No usar permisos temporales para tareas recurrentes",
            "Revocar si cambia el alcance",
        ],
        "evidence": "solicitud, aprobador, zona, fecha de inicio, fecha de fin y confirmación de revocación.",
        "escalation": "Cualquier excepción debe quedar aprobada por Seguridad y registrada en HelpOps si genera incidencia.",
    },
    {
        "id": 15,
        "title": "Revisión de KPIs operativos en OpsLake",
        "document_type": "faq operativa",
        "department": "Politicas internas",
        "affected_system": "OpsLake",
        "objective": "Define cómo interpretar indicadores de productividad, cumplimiento de rutas, exactitud de stock y tiempos de resolución.",
        "scope": "Aplica a comités diarios, revisión de turno, seguimiento de SLA interno y detección de datos incompletos.",
        "steps": [
            "Seleccionar centro y ventana temporal",
            "Revisar fuente del indicador",
            "Comparar dato agregado con sistema transaccional",
            "Documentar desviaciones antes de presentar conclusión",
        ],
        "controls": [
            "No usar KPI si falta actualización de origen",
            "No mezclar centros sin normalizar calendario",
            "Señalar datos provisionales",
        ],
        "evidence": "panel, filtro aplicado, fecha de extracción, sistema origen y comentario de desviación.",
        "escalation": "Si un KPI contradice el dato operativo, abrir incidencia de datos en HelpOps y no usarlo para decisiones críticas.",
    },
    {
        "id": 16,
        "title": "Gestión de incidencias por transportista",
        "document_type": "guía de escalado",
        "department": "Seguridad",
        "affected_system": "RutaNexo TMS",
        "objective": "Establece cómo clasificar retrasos, rechazos, ausencias de conductor y problemas documentales del transportista.",
        "scope": "Aplica a transporte contratado, transportista alternativo, ruta subcontratada y cierre de servicio con documentación pendiente.",
        "steps": [
            "Registrar transportista y ruta",
            "Clasificar incidencia por demora, documentación o rechazo",
            "Aplicar plan alternativo si afecta ventana crítica",
            "Cerrar con evidencia de recuperación",
        ],
        "controls": [
            "No penalizar sin evidencia",
            "Separar causa interna de causa del transportista",
            "Relacionar POD o CMR pendiente",
        ],
        "evidence": "ruta, transportista, hora comprometida, causa, acción correctiva y documento asociado.",
        "escalation": "Si el transportista acumula tres incidencias relevantes, Escalado revisa la continuidad del servicio.",
    },
    {
        "id": 17,
        "title": "Procedimiento de inventario cíclico",
        "document_type": "checklist",
        "department": "Onboarding",
        "affected_system": "AlmaTrack WMS",
        "objective": "Checklist operativo para ejecutar inventario cíclico sin interrumpir oleadas críticas de preparación.",
        "scope": "Aplica a nuevos coordinadores, supervisores de almacén y equipos de apoyo durante conteos planificados.",
        "steps": [
            "Bloquear ubicaciones seleccionadas",
            "Asignar contador y revisor",
            "Registrar cantidad física",
            "Resolver diferencias antes de liberar ubicación",
        ],
        "controls": [
            "No contar ubicaciones con tarea activa",
            "No aceptar conteo sin revisor",
            "Conservar histórico del ajuste",
        ],
        "evidence": "lista de ubicaciones, contador, revisor, diferencias y validación final.",
        "escalation": "Si el conteo impacta expediciones abiertas, coordinar con cierre de expediciones antes de liberar stock.",
    },
    {
        "id": 18,
        "title": "Checklist de apertura y cierre de turno",
        "document_type": "checklist",
        "department": "Onboarding",
        "affected_system": "HelpOps",
        "objective": "Resume controles diarios para iniciar y cerrar turno con incidencias, handover y sistemas críticos revisados.",
        "scope": "Aplica a coordinadores nuevos, responsables de turno y equipos que reciben relevo entre almacén, transporte y soporte.",
        "steps": [
            "Revisar tickets abiertos",
            "Confirmar pedidos y rutas críticas",
            "Validar accesos necesarios",
            "Registrar handover con riesgos y responsables",
        ],
        "controls": [
            "No cerrar turno con incidente crítico sin dueño",
            "No omitir sistemas con alertas",
            "Separar pendiente operativo de pendiente técnico",
        ],
        "evidence": "lista de pendientes, responsable de cada acción, ticket relacionado y hora de revisión.",
        "escalation": "Si el relevo no puede asumir un pendiente, abrir o actualizar ticket en HelpOps antes de salir del turno.",
    },
    {
        "id": 19,
        "title": "Gestión de documentos incompletos o ilegibles",
        "document_type": "guía de diagnóstico",
        "department": "Onboarding",
        "affected_system": "ScanBridge IDP",
        "objective": "Explica cómo tratar documentos escaneados con baja calidad, campos ausentes o contradicciones entre imagen y dato extraído.",
        "scope": "Aplica a recepción, POD, CMR, devolución, alta documental y expedientes de onboarding con anexos obligatorios.",
        "steps": [
            "Identificar campo afectado",
            "Revisar imagen original",
            "Solicitar nueva captura si falta dato crítico",
            "Marcar documento como pendiente si no se puede validar",
        ],
        "controls": [
            "No corregir campos críticos sin evidencia",
            "No cerrar expediente con firma ilegible",
            "Relacionar documento con pedido, ruta o persona según proceda",
        ],
        "evidence": "documento original, campo dudoso, decisión de revisión y motivo de rechazo o aceptación.",
        "escalation": "Si el problema afecta muchos documentos del mismo lote de escaneo, crear ticket de análisis en HelpOps.",
    },
    {
        "id": 20,
        "title": "Política de trazabilidad y fuentes del asistente interno",
        "document_type": "política",
        "department": "Politicas internas",
        "affected_system": "DocuFlow",
        "objective": "Define cómo debe responder el asistente interno cuando usa documentación, incidencias resueltas o casos abiertos como evidencia.",
        "scope": "Aplica a respuestas de soporte operativo, consultas de managers, dudas de onboarding y análisis de incidentes similares.",
        "steps": [
            "Citar fuente documental o incidencia usada",
            "Distinguir procedimiento vigente de caso similar",
            "Pedir aclaración si falta sistema, proceso o error exacto",
            "Ofrecer registro de incidencia si no hay conocimiento suficiente",
        ],
        "controls": [
            "No presentar un caso parecido como solución definitiva",
            "No usar fuentes de sistemas distintos sin advertirlo",
            "No inventar pasos ausentes del corpus",
        ],
        "evidence": "identificador de documento o incidencia, fragmento relevante, estado del caso y motivo de confianza.",
        "escalation": "Si el asistente no encuentra evidencia directa, debe explicar la limitación y proponer recopilar datos mínimos para registrar un nuevo caso.",
    },
]

SYSTEM_PROFILES = {
    "AlmaTrack WMS": {
        "department": "Operaciones",
        "count": 20,
        "category": "almacen",
        "tags": ["almacen", "wms"],
        "scenarios": [
            ("Cierre de expedición con tareas RF abiertas", "cierre de expedición", "la expedición no permitía cierre porque dos tareas RF quedaban pendientes", "salidas retenidas en muelle y retraso de carga", "cerrar tareas huérfanas, revisar bultos leídos y confirmar expedición desde la consola de supervisor", "el panel de seguimiento recibió el estado expedido"),
            ("Diferencia de stock tras conteo cíclico", "conteo cíclico", "el stock físico no coincidía con reservado y disponible", "picking detenido en el pasillo afectado", "bloquear ubicación, reconciliar movimientos y aprobar ajuste con segundo conteo", "las cantidades quedaron alineadas y la ubicación fue liberada"),
            ("Picking dirigido a ubicación vacía", "preparación de pedidos", "el terminal RF enviaba al operario a una ubicación sin stock visible", "pedidos urgentes acumulados sin preparar", "recalcular reserva, reasignar ubicación y anular la tarea antigua", "el operario confirmó picking correcto en la nueva ubicación"),
            ("Packing duplicado en salida de paquetería", "embalaje y etiquetado", "dos etiquetas quedaban asociadas al mismo bulto", "riesgo de expedición duplicada y rechazo del transportista", "anular etiqueta duplicada y regenerar secuencia de packing", "el manifiesto final mostró un único bulto válido"),
            ("Recepción parcial sin ubicación de revisión", "recepción de mercancía", "la entrada parcial quedaba recibida pero sin ubicación de revisión", "mercancía parada en muelle sin disponibilidad ni cuarentena", "crear ubicación temporal de revisión y asociar bultos pendientes", "la recepción quedó visible para control de calidad"),
            ("Movimiento interno sin histórico completo", "reubicación interna", "el movimiento aparecía aplicado pero sin trazabilidad completa", "supervisión no podía auditar el cambio de ubicación", "regenerar histórico de movimiento y validar usuario responsable", "el historial mostró origen, destino y hora de confirmación"),
            ("Reposición bloqueada por lote en cuarentena", "reposición", "la tarea intentaba usar un lote bloqueado por calidad", "preparación de pedidos críticos quedó sin stock alternativo", "excluir lote bloqueado y recalcular reposición desde lote liberado", "QualiTrace QMS y WMS quedaron alineados"),
            ("Muelle ocupado por expedición ya retirada", "gestión de muelles", "el muelle seguía reservado para una expedición que ya salió", "nuevas cargas no podían asignar puerta", "liberar reserva del muelle y cerrar evento físico", "las puertas disponibles volvieron al tablero"),
        ],
    },
    "LogiCore ERP": {
        "department": "Operaciones",
        "count": 16,
        "category": "pedidos",
        "tags": ["erp", "pedidos"],
        "scenarios": [
            ("Pedido retenido por regla manual", "liberación de pedido", "el pedido seguía retenido aunque los datos obligatorios estaban completos", "preparación no recibía la orden de trabajo", "revisar regla aplicada, añadir motivo de liberación y reenviar pedido a almacén", "AlmaTrack WMS recibió la orden y el estado pasó a preparación"),
            ("Factura interna con líneas desordenadas", "validación de factura", "la factura agrupaba líneas en orden distinto al pedido", "backoffice no podía cerrar conciliación", "reordenar líneas por referencia y regenerar prevalidación interna", "pedido y factura quedaron comparables"),
            ("Pedido intercentro con estado contradictorio", "sincronización intercentro", "origen indicaba expedido y destino seguía retenido", "seguimiento perdió visibilidad de tránsito", "republicar evento de expedición y confirmar recepción lógica", "ambos centros mostraron el mismo estado"),
            ("Proveedor con aviso de entrada incompleto", "compras y recepción", "el aviso de entrada no traía ventana ni muelle asignado", "recepción tuvo que aparcar la mercancía", "completar aviso, asignar muelle y notificar a almacén", "la recepción se vinculó al aviso correcto"),
            ("Reserva de stock no liberada tras cancelación", "gestión de stock", "la cancelación del pedido no devolvió stock disponible", "otros pedidos quedaban bloqueados sin causa real", "liberar reserva residual y actualizar disponible", "los pedidos pendientes pudieron reservar stock"),
            ("Devolución de cliente sin motivo operativo", "devoluciones", "el expediente de devolución no tenía motivo clasificado", "calidad y almacén no sabían el destino del producto", "clasificar motivo, crear ubicación de revisión y vincular pedido", "la devolución quedó lista para dictamen"),
        ],
    },
    "RutaNexo TMS": {
        "department": "Operaciones",
        "count": 16,
        "category": "transporte",
        "tags": ["tms", "rutas"],
        "scenarios": [
            ("Ruta congelada tras parada prioritaria", "replanificación de ruta", "la ruta quedaba calculando indefinidamente tras mover una parada urgente", "conductores sin secuencia final y ventanas en riesgo", "restaurar versión previa, revisar restricción de parada y publicar nueva secuencia", "el conductor recibió la versión actualizada"),
            ("Transportista alternativo sin confirmación", "reasignación de transportista", "la ruta se reasignaba pero el transportista no recibía confirmación", "carga preparada sin vehículo asignado", "revalidar contrato operativo y reenviar aceptación de servicio", "la torre de control vio aceptación final"),
            ("POD pendiente en cierre de ruta", "prueba de entrega", "la ruta aparecía entregada pero sin POD asociado", "facturación interna y atención al cliente no podían cerrar caso", "solicitar justificante, validar parada y asociar documento", "el expediente mostró POD revisado"),
            ("CMR ilegible rechazado por backoffice", "documentación de transporte", "el CMR escaneado no permitía leer matrícula ni firma", "se bloqueó el cierre documental del viaje", "pasar documento a ScanBridge IDP y pedir nueva captura si no validaba", "el documento corregido quedó vinculado"),
            ("Entrega fallida sin motivo codificado", "incidencia de entrega", "el conductor marcó fallo sin motivo operativo", "la reposición de ruta quedó sin decisión", "clasificar motivo, programar segunda visita y registrar observación", "la nueva parada quedó planificada"),
            ("Secuencia de paradas publicada con ETA incoherente", "control de ruta", "el ETA final era anterior a una parada intermedia", "la torre de control no podía confiar en la promesa", "recalcular con restricciones reales y validar ventanas", "los ETA quedaron ordenados"),
        ],
    },
    "HelpOps": {
        "department": "Seguridad",
        "count": 9,
        "category": "soporte",
        "tags": ["helpops", "tickets"],
        "scenarios": [
            ("Ticket crítico sin equipo responsable", "triage de incidencias", "el ticket quedó en cola general aunque impactaba expediciones", "nadie asumía la recuperación del servicio", "reasignar a soporte funcional y añadir responsable de guardia", "la cola mostró dueño y SLA correcto"),
            ("Duplicado de incidencia sin relación", "gestión de tickets", "dos equipos abrieron tickets por el mismo síntoma sin vincularlos", "se duplicó investigación y comunicación", "relacionar tickets, cerrar duplicado y centralizar evidencias", "el historial quedó consolidado"),
            ("Prioridad baja en parada operativa", "clasificación de impacto", "un bloqueo de salida fue clasificado como consulta menor", "el SLA no reflejaba la parada real", "ajustar prioridad por impacto y documentar criterio", "el tablero mostró prioridad alta"),
            ("Cierre sin evidencia de recuperación", "control de calidad de soporte", "el ticket se cerró sin confirmar recuperación operativa", "el incidente reapareció en el turno siguiente", "reabrir caso, pedir validación y registrar prueba de cierre", "el cierre quedó asociado a evidencia"),
        ],
    },
    "SafeGate": {
        "department": "Seguridad",
        "count": 9,
        "category": "accesos",
        "tags": ["safegate", "seguridad"],
        "scenarios": [
            ("Acceso temporal rechazado en visita técnica", "alta temporal de visita", "el torno rechazaba credenciales temporales vigentes", "mantenimiento no pudo entrar a tiempo", "recalcular grupo de acceso, validar zona y emitir credencial nueva", "la visita accedió con caducidad correcta"),
            ("Permiso nocturno sin propagación completa", "turno nocturno", "la consola mostraba permiso activo pero el lector seguía denegando entrada", "relevo de guardia quedó retrasado", "sincronizar permisos de lector y revisar ventana horaria", "la entrada funcionó en prueba supervisada"),
            ("Baja de usuario no reflejada en zona restringida", "baja operativa", "el usuario seguía apareciendo autorizado en una zona secundaria", "riesgo de acceso indebido", "revocar grupos residuales y confirmar baja por zona", "la revisión mostró permisos vacíos"),
            ("Proveedor recurrente con credencial caducada", "control de proveedores", "la tarjeta figuraba caducada pese a tener servicio programado", "entrada de proveedor quedó manual", "crear credencial nueva con responsable y caducidad", "el registro quedó asociado al servicio"),
        ],
    },
    "ScanBridge IDP": {
        "department": "Operaciones",
        "count": 8,
        "category": "documentacion",
        "tags": ["scanbridge", "ocr"],
        "scenarios": [
            ("OCR de POD sin firma reconocida", "validación documental", "el campo de firma quedaba vacío aunque la imagen la mostraba parcialmente", "cierre de entrega quedó en revisión", "marcar campo dudoso, pedir validación manual y asociar imagen revisada", "el POD fue aceptado con observación"),
            ("CMR con matrícula mal extraída", "extracción de CMR", "la matrícula extraída no coincidía con la ruta", "backoffice rechazó el documento", "corregir matrícula desde imagen original y bloquear aprendizaje del caso", "la ruta quedó vinculada al CMR correcto"),
            ("Albarán de recepción con cantidad borrosa", "recepción documental", "la cantidad extraída no era fiable", "recepción no podía cerrar entrada", "solicitar nueva captura y dejar documento pendiente", "la segunda imagen permitió validar cantidad"),
            ("Documento mezclado entre dos rutas", "clasificación documental", "dos páginas de rutas distintas acabaron en el mismo expediente", "se contaminó la evidencia de entrega", "separar páginas, reasignar expediente y registrar corrección", "cada ruta quedó con su documento"),
        ],
    },
    "QualiTrace QMS": {
        "department": "Seguridad",
        "count": 7,
        "category": "calidad",
        "tags": ["qualitrace", "calidad"],
        "scenarios": [
            ("Lote bloqueado por embalaje dañado", "bloqueo de lote", "Calidad bloqueó lote por daño visible en embalaje", "pedidos preparados no podían salir", "bloquear ubicaciones, registrar dictamen y sustituir producto en pedidos críticos", "LogiCore ERP y WMS mostraron bloqueo coherente"),
            ("No conformidad sin dictamen final", "cierre de no conformidad", "el caso tenía evidencias pero no dictamen de liberación", "almacén dudaba si mover el lote", "solicitar decisión de Calidad y mantener cuarentena", "el lote quedó cerrado como liberado o rechazado"),
            ("Temperatura fuera de rango en recepción", "control de calidad", "el registro de temperatura superó el umbral interno", "mercancía quedó retenida en muelle", "crear no conformidad, bloquear lote y tomar muestra", "la decisión quedó asociada al lote"),
            ("Auditoría interna con evidencia incompleta", "auditoría", "faltaban fotos y comentario del supervisor", "el hallazgo no podía cerrarse", "solicitar evidencia adicional y actualizar expediente", "auditoría validó el cierre"),
        ],
    },
    "OnboardHub": {
        "department": "Onboarding",
        "count": 5,
        "category": "onboarding",
        "tags": ["onboardhub", "alta"],
        "scenarios": [
            ("Checklist de coordinador sin mentor", "onboarding de coordinador", "el expediente no permitía cierre porque no había mentor asignado", "la persona no podía asumir turno en solitario", "asignar mentor, registrar turno de sombra y validar permisos", "el expediente pasó a listo para operar"),
            ("Curso operativo pendiente de acuse", "formación inicial", "el curso aparecía completado pero sin acuse de lectura", "Onboarding no podía cerrar la incorporación", "republicar módulo y solicitar confirmación del participante", "el acuse quedó en el expediente"),
            ("Permisos iniciales incompletos", "alta de usuario", "faltaba acceso a uno de los sistemas críticos del rol", "el nuevo coordinador dependía de otro usuario", "comparar rol contra matriz y pedir alta faltante en SafeGate", "el checklist mostró permisos completos"),
        ],
    },
    "OpsLake": {
        "department": "Politicas internas",
        "count": 5,
        "category": "analitica",
        "tags": ["opslake", "kpi"],
        "scenarios": [
            ("KPI de expediciones con dato incompleto", "revisión de KPI", "el indicador diario no incluía el último turno", "dirección veía cumplimiento menor al real", "marcar dato como provisional y recargar origen operativo", "el panel mostró fecha de actualización correcta"),
            ("Diferencia entre productividad y tareas cerradas", "control analítico", "la productividad agregada no cuadraba con tareas de WMS", "supervisión dudaba del rendimiento reportado", "comparar fuentes y excluir tareas reabiertas", "el KPI quedó reconciliado"),
            ("Panel de rutas sin filtro de centro", "seguimiento de transporte", "el panel mezclaba rutas de dos centros", "se tomaban decisiones con dato contaminado", "aplicar filtro de centro y publicar nota de corrección", "los valores quedaron separados"),
        ],
    },
    "DocuFlow": {
        "department": "Politicas internas",
        "count": 5,
        "category": "documentacion",
        "tags": ["docuflow", "procedimientos"],
        "scenarios": [
            ("Procedimiento publicado con versión antigua", "control documental", "la portada indicaba versión nueva pero el contenido era anterior", "equipos seguían pasos retirados", "republicar versión completa y retirar copia obsoleta", "los usuarios vieron solo la versión vigente"),
            ("Anexo obligatorio no disponible", "gestión documental", "el documento principal apuntaba a un anexo no accesible", "el equipo no podía completar el control", "adjuntar anexo vigente y registrar cambio", "el procedimiento abrió todos los anexos"),
            ("Acuse de lectura no registrado", "lectura de política", "usuarios abrían política pero no quedaba confirmación", "cumplimiento no podía demostrar difusión", "reindexar control de lectura y pedir confirmación", "la lectura quedó registrada"),
        ],
    },
}

PROFILE_ORDER = [
    "SafeGate",
    "AlmaTrack WMS",
    "LogiCore ERP",
    "RutaNexo TMS",
    "HelpOps",
    "ScanBridge IDP",
    "QualiTrace QMS",
    "OnboardHub",
    "OpsLake",
    "DocuFlow",
]

OPEN_BY_SYSTEM = {
    "AlmaTrack WMS": ("Ubicación RF inconsistente después de reposición", "reposición y picking", "RF muestra ubicación vacía mientras la consola mantiene stock disponible", "se detiene la preparación de pedidos urgentes"),
    "LogiCore ERP": ("Pedido bloqueado tras doble validación", "liberación de pedido", "el pedido mantiene retención aunque el historial muestra validación completa", "no existe fecha fiable de salida"),
    "RutaNexo TMS": ("Recalculo masivo pendiente por incidencia meteorológica", "replanificación masiva", "varias rutas quedan en cola sin secuencia final", "los vehículos no reciben instrucciones de salida"),
    "HelpOps": ("Ticket de parada operativa sin diagnóstico reproducible", "triage crítico", "el síntoma aparece y desaparece según el turno", "soporte no puede confirmar causa raíz"),
    "SafeGate": ("Permiso temporal no revocado tras servicio externo", "cierre de permiso", "la credencial sigue válida después de la hora prevista", "Seguridad mantiene riesgo operativo abierto"),
    "ScanBridge IDP": ("Extracción de lote documental con campos cruzados", "validación OCR", "campos de pedido y ruta se cruzan entre documentos", "backoffice no puede cerrar expedientes"),
    "QualiTrace QMS": ("Lote en cuarentena sin dictamen de liberación", "no conformidad", "Calidad aún no emitió decisión final", "pedidos relacionados permanecen retenidos"),
    "OnboardHub": ("Expediente de coordinador alterna entre pendiente y revisión", "cierre de onboarding", "el checklist cambia de estado sin acción visible", "la incorporación no puede programarse"),
    "OpsLake": ("Panel de cumplimiento con actualización parcial", "revisión de KPI", "la ventana diaria no carga todas las fuentes", "el comité no puede usar el dato como referencia"),
    "DocuFlow": ("Guía operativa con anexo interno no disponible", "consulta documental", "el procedimiento abre pero el anexo obligatorio no aparece", "el equipo no puede ejecutar el control documentado"),
}

SITES = [
    "centro norte",
    "hub peninsular",
    "almacén este",
    "centro metropolitano",
    "plataforma sur",
    "torre de control",
    "muelle 4",
    "zona de preparación",
    "backoffice de tarde",
    "relevo de madrugada",
]
OPENINGS = [
    "Durante la operación en {site}, el equipo detectó un fallo en {process}: {symptom}.",
    "En {site} se abrió una incidencia mientras se ejecutaba {process}; el síntoma observado fue que {symptom}.",
    "El responsable de turno reportó en {site} que, durante {process}, {symptom}.",
    "La revisión de {process} en {site} dejó evidencia de que {symptom}.",
]
RESOLUTION_TEMPLATES = [
    "Se reprodujo el caso con la referencia afectada, se aplicó la corrección operativa y se documentó la validación posterior: {action}. Como verificación, {verification}.",
    "El equipo aisló la causa funcional, ejecutó la recuperación controlada y dejó una nota de aprendizaje para casos similares: {action}. Después, {verification}.",
    "La resolución consistió en normalizar el dato origen, repetir el paso operativo y cerrar con doble comprobación: {action}. La prueba de cierre confirmó que {verification}.",
    "Se corrigió el estado inconsistente sin saltarse controles de auditoría: {action}. El cierre quedó aceptado cuando {verification}.",
]
PRIORITIES = ["medium", "high", "medium", "low", "high", "medium", "critical", "medium"]


def build_seed_documents() -> list[dict]:
    documents: list[dict] = []
    for item in DOCUMENT_BLUEPRINTS:
        content = build_document_content(**{key: item[key] for key in ("title", "system", "department", "objective", "scope", "steps", "controls", "evidence", "escalation")} if "system" in item else {
            "title": item["title"],
            "system": item["affected_system"],
            "department": item["department"],
            "objective": item["objective"],
            "scope": item["scope"],
            "steps": item["steps"],
            "controls": item["controls"],
            "evidence": item["evidence"],
            "escalation": item["escalation"],
        })
        documents.append(
            {
                "id": item["id"],
                "title": item["title"],
                "document_type": item["document_type"],
                "department": item["department"],
                "affected_system": item["affected_system"],
                "content": content,
                "tags": [
                    item["department"].lower().replace(" ", "-"),
                    slug(item["affected_system"]),
                    slug(item["document_type"]),
                    slug(item["title"].split()[0]),
                ],
                "source_url": None,
                "created_at": iso(item["id"]),
                "updated_at": iso(item["id"], 2),
            }
        )
    return documents


def build_seed_tickets() -> list[dict]:
    resolved: list[dict] = []
    for system in PROFILE_ORDER:
        profile = SYSTEM_PROFILES[system]
        scenarios = profile["scenarios"]
        for index in range(profile["count"] - 1):
            title, process, symptom, impact, action, verification = scenarios[index % len(scenarios)]
            site = SITES[(index + len(system)) % len(SITES)]
            variant = index // len(scenarios)
            if variant:
                title = f"{title} en revisión repetida {variant + 1}"
            description = OPENINGS[index % len(OPENINGS)].format(site=site, process=process, symptom=symptom)
            description += (
                f" El impacto fue concreto: {impact}. Se relacionó con {profile['category']} y quedó documentado "
                "con referencias internas de turno, centro y sistema afectado."
            )
            description += (
                f" La revisión comparó operación esperada, registros de {system}, evidencias de usuario "
                "y controles definidos en los procedimientos vinculados."
            )
            resolution = RESOLUTION_TEMPLATES[index % len(RESOLUTION_TEMPLATES)].format(action=action, verification=verification)
            resolved.append(
                {
                    "title": title,
                    "description": description,
                    "department": profile["department"],
                    "category": profile["category"],
                    "affected_system": system,
                    "priority": PRIORITIES[(index + len(system)) % len(PRIORITIES)],
                    "status": "resolved",
                    "is_resolved": True,
                    "resolution": resolution,
                    "impact": impact,
                    "expected_behavior": f"El proceso de {process} debe completarse con estado coherente, trazabilidad suficiente y sin intervención manual no documentada.",
                    "actual_behavior": symptom,
                    "tags": sorted(set(profile["tags"] + [slug(process), "resuelto"])),
                    "source": "seed_dataset",
                    "source_url": None,
                }
            )

    tickets: list[dict] = []
    for index, item in enumerate(resolved, start=1):
        created_at = iso(30 + index, index % 8)
        resolved_at = iso(30 + index, (index % 8) + 5)
        tickets.append(
            {
                "id": index,
                "external_id": f"INC-SC-{index:04d}",
                **item,
                "title": f"{item['title']} #{index:03d}",
                "created_by": "seed-loader",
                "created_at": created_at,
                "resolved_at": resolved_at,
                "updated_at": resolved_at,
            }
        )

    first_open_id = len(tickets) + 1
    for offset, (system, (title, process, symptom, impact)) in enumerate(OPEN_BY_SYSTEM.items()):
        profile = SYSTEM_PROFILES[system]
        identifier = first_open_id + offset
        created_at = iso(30 + identifier, identifier % 8)
        description = (
            f"Incidencia abierta en {SITES[offset]} durante {process}: {symptom}. "
            f"El equipo aún no dispone de una causa confirmada ni de una corrección validada. Impacto actual: {impact}."
        )
        description += (
            f" Se han revisado evidencias iniciales de {system}, pero falta información concreta para decidir si "
            "se trata de un caso conocido, una desviación de datos o un error nuevo del proceso."
        )
        tickets.append(
            {
                "id": identifier,
                "external_id": f"INC-SC-{identifier:04d}",
                "title": f"{title} #{identifier:03d}",
                "description": description,
                "department": profile["department"],
                "category": profile["category"],
                "affected_system": system,
                "priority": "high" if offset % 3 else "critical",
                "status": "open",
                "is_resolved": False,
                "impact": impact,
                "expected_behavior": f"El proceso de {process} debe disponer de diagnóstico, responsable y siguiente paso operativo antes de considerarse resuelto.",
                "actual_behavior": symptom,
                "tags": sorted(set(profile["tags"] + [slug(process), "abierto", "pendiente-diagnostico"])),
                "created_by": "seed-loader",
                "created_at": created_at,
                "updated_at": iso(30 + identifier, (identifier % 8) + 3),
                "source": "seed_dataset",
                "source_url": None,
            }
        )
    return tickets


def main() -> int:
    documents = build_seed_documents()
    tickets = build_seed_tickets()
    if len(documents) != 20 or len(tickets) != 100:
        raise RuntimeError("Generated seed counts are invalid")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "seed_documents.json").write_text(json.dumps(documents, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (DATA_DIR / "seed_tickets.json").write_text(json.dumps(tickets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Documents: {len(documents)} {dict(Counter(item['affected_system'] for item in documents))}")
    print(f"Tickets: {len(tickets)} {dict(Counter(item['affected_system'] for item in tickets))}")
    print("Resolved tickets:", sum(1 for item in tickets if item["is_resolved"]))
    print("Unresolved tickets:", sum(1 for item in tickets if not item["is_resolved"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
