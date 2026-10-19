from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"

ALLOWED_SYSTEMS = [
    "LogiCore ERP",
    "AlmaTrack WMS",
    "RutaNexo TMS",
    "HelpOps",
    "DocuFlow",
    "OnboardHub",
    "SafeGate",
    "QualiTrace QMS",
    "ScanBridge IDP",
    "OpsLake",
]
ALLOWED_DOCUMENT_DISTRIBUTION = {
    "Operaciones": 8,
    "Seguridad": 6,
    "Onboarding": 4,
    "Politicas internas": 2,
}
TICKETS_FILE = "seed_tickets.json"
DOCUMENTS_FILE = "seed_documents.json"
_BASE_CREATED_AT = datetime(2026, 1, 6, 8, 0, tzinfo=UTC)


class SeedDataValidationError(ValueError):
    pass


@dataclass(slots=True)
class SeedDataSummary:
    tickets: int
    resolved_tickets: int
    unresolved_tickets: int
    documents: int
    document_distribution: dict[str, int]


def _iso_datetime(offset_days: int, *, hours: int = 0) -> str:
    return (_BASE_CREATED_AT + timedelta(days=offset_days, hours=hours)).isoformat()


def _join_paragraphs(*paragraphs: str) -> str:
    return "\n\n".join(paragraph.strip() for paragraph in paragraphs if paragraph.strip())


def _repair_mojibake_text(value: str) -> str:
    if not any(marker in value for marker in ("Ã", "Â", "â")):
        return value
    try:
        return value.encode("latin-1").decode("utf-8")
    except UnicodeError:
        return value


def _repair_mojibake(value: Any) -> Any:
    if isinstance(value, str):
        return _repair_mojibake_text(value)
    if isinstance(value, list):
        return [_repair_mojibake(item) for item in value]
    if isinstance(value, dict):
        return {key: _repair_mojibake(item) for key, item in value.items()}
    return value


def _document_content(*, title: str, department: str, system: str, objective: str, controls: list[str], escalation: str) -> str:
    controls_text = " ".join(controls)
    return _join_paragraphs(
        (
            f"{title} es un documento interno de {department} para el sistema {system}. {objective} "
            "La referencia se utiliza solo dentro del entorno ficticio de la empresa y no debe compartirse fuera de los equipos de operaciones, seguridad o soporte."
        ),
        (
            f"El procedimiento indica que cualquier cambio ejecutado en {system} debe dejar trazabilidad operativa con fecha, turno, almacén o centro afectado, "
            "usuario responsable y validación posterior por parte del rol supervisor. La operación no se considera cerrada hasta confirmar que el dato final quedó sincronizado en los paneles internos y en los listados de seguimiento diario."
        ),
        (
            f"Controles obligatorios: {controls_text} "
            "Si alguno de los controles falla, el operador debe pausar la acción, registrar la desviación en DocuFlow o en el sistema correspondiente y notificarlo al responsable del turno antes de continuar."
        ),
        (
            "El documento también recuerda que las consultas sobre permisos, datos maestros, credenciales temporales o movimientos masivos nunca deben resolverse con atajos manuales no documentados. "
            "Toda excepción operativa debe quedar anotada con motivo, alcance, duración prevista, aprobador y evidencia de cierre para auditoría interna."
        ),
        (
            f"Escalado y seguimiento: {escalation} "
            "La versión vigente sustituye cualquier copia local previa y debe revisarse siempre desde el repositorio interno antes de ejecutar tareas críticas o responder dudas en el asistente."
        ),
    )


def _ticket_description(*, title: str, system: str, department: str, site: str, process: str, symptom: str, impact: str) -> str:
    return _join_paragraphs(
        (
            f"Se reportó la incidencia \"{title}\" en el sistema {system} durante la operación del área de {department}. "
            f"El caso se detectó en {site} mientras el equipo ejecutaba el proceso de {process} y varios usuarios observaron {symptom}."
        ),
        (
            f"El impacto inicial fue el siguiente: {impact} "
            "Los coordinadores confirmaron que el problema no provenía de una plataforma externa real, sino de un flujo ficticio del entorno de demo diseñado para simular casuísticas internas de logística, seguridad y onboarding."
        ),
        (
            f"En la revisión inicial se validaron permisos, colas de trabajo, reglas de negocio, estado de sincronización y registros de control asociados a {system}. "
            "La evidencia quedó documentada con capturas internas, observaciones del turno y una descripción paso a paso para que el incidente pudiera indexarse después como conocimiento reutilizable."
        ),
    )


def _ticket_resolution(*, system: str, corrective_action: str, verification: str) -> str:
    return _join_paragraphs(
        (
            f"Se aplicó la acción correctiva principal en {system}: {corrective_action}. "
            "La intervención se realizó dentro de la ventana autorizada por el supervisor y se documentó con hora de inicio, usuario responsable y referencia de cambio."
        ),
        (
            f"Validación posterior: {verification}. "
            "Tras la corrección se repitió el flujo operativo completo con datos ficticios de prueba y se confirmó que no quedaban mensajes de error, bloqueos residuales ni divergencias en los listados internos."
        ),
    )


def _resolved_ticket_payload(*, identifier: int, title: str, department: str, category: str, system: str, site: str, process: str, symptom: str, impact: str, corrective_action: str, verification: str, priority: str, tags: list[str]) -> dict:
    created_at = _iso_datetime(identifier - 1)
    resolved_at = _iso_datetime(identifier - 1, hours=6)
    return {
        "id": identifier,
        "external_id": f"INC-S{identifier:05d}",
        "title": f"{title} #{identifier:03d}",
        "description": _ticket_description(
            title=title,
            system=system,
            department=department,
            site=site,
            process=process,
            symptom=symptom,
            impact=impact,
        ),
        "department": department,
        "category": category,
        "affected_system": system,
        "priority": priority,
        "status": "resolved",
        "is_resolved": True,
        "resolution": _ticket_resolution(
            system=system,
            corrective_action=corrective_action,
            verification=verification,
        ),
        "impact": impact,
        "expected_behavior": f"El proceso de {process} debe completarse sin errores y con trazabilidad íntegra en {system}.",
        "actual_behavior": symptom,
        "tags": tags,
        "created_by": "seed-loader",
        "created_at": created_at,
        "resolved_at": resolved_at,
        "updated_at": resolved_at,
        "source": "seed_dataset",
        "source_url": f"https://intranet.logistica.local/incidents/{identifier:03d}",
    }


def _open_ticket_payload(*, identifier: int, title: str, department: str, category: str, system: str, site: str, process: str, symptom: str, impact: str, priority: str, tags: list[str]) -> dict:
    created_at = _iso_datetime(identifier - 1)
    updated_at = _iso_datetime(identifier - 1, hours=3)
    return {
        "id": identifier,
        "external_id": f"INC-S{identifier:05d}",
        "title": f"{title} #{identifier:03d}",
        "description": _ticket_description(
            title=title,
            system=system,
            department=department,
            site=site,
            process=process,
            symptom=symptom,
            impact=impact,
        ),
        "department": department,
        "category": category,
        "affected_system": system,
        "priority": priority,
        "status": "open",
        "is_resolved": False,
        "impact": impact,
        "expected_behavior": f"El proceso de {process} debe mantenerse estable y responder dentro del tiempo estándar definido para {system}.",
        "actual_behavior": symptom,
        "tags": tags,
        "created_by": "seed-loader",
        "created_at": created_at,
        "updated_at": updated_at,
        "source": "seed_dataset",
        "source_url": f"https://intranet.logistica.local/incidents/{identifier:03d}",
    }


def build_seed_documents() -> list[dict]:
    templates = [
        {
            "title": "Registro de entregas parciales en ventana crítica",
            "document_type": "procedimiento",
            "department": "Operaciones",
            "affected_system": "LogiCore ERP",
            "objective": "Describe cómo registrar expediciones parciales sin perder la promesa de servicio ni la trazabilidad del pedido cuando un camión sale con parte de la carga pendiente.",
            "controls": [
                "marcar los bultos expedidos y los retenidos por separado",
                "confirmar la actualización del pedido y del panel de incidencias del turno",
                "adjuntar observación sobre la causa operativa y la hora estimada del siguiente envío",
            ],
            "escalation": "Si el pedido queda bloqueado durante más de diez minutos, Operaciones debe escalarlo a soporte funcional de LogiCore ERP.",
        },
        {
            "title": "Replanificación de rutas tras cierre de carretera",
            "document_type": "procedimiento",
            "department": "Operaciones",
            "affected_system": "RutaNexo",
            "objective": "Establece el flujo para recalcular rutas afectadas por restricciones viales, cambios de secuencia o incidencias que obligan a rehacer la asignación del conductor.",
            "controls": [
                "revisar restricciones del vehículo y del conductor antes de recalcular",
                "validar que cada parada mantenga ventana, prioridad y observaciones",
                "publicar la nueva secuencia solo después de revisar impactos en la torre de control",
            ],
            "escalation": "Si la ruta no publica una nueva secuencia válida, debe abrirse incidencia de prioridad alta y avisar al coordinador regional.",
        },
        {
            "title": "Conteo cíclico con diferencia de inventario",
            "document_type": "procedimiento",
            "department": "Operaciones",
            "affected_system": "AlmaTrack WMS",
            "objective": "Define cómo aislar ubicaciones con diferencias de inventario y cómo registrar la cuarentena operativa hasta cerrar la validación con el supervisor de almacén.",
            "controls": [
                "poner la ubicación en cuarentena antes de liberar nuevas tareas",
                "comparar stock físico, stock reservado y stock en tránsito",
                "cerrar la diferencia solo con la aprobación del supervisor asignado",
            ],
            "escalation": "Si la diferencia supera el umbral del turno o afecta varios pasillos, se eleva al responsable del centro.",
        },
        {
            "title": "Gestión de pedidos retenidos por validación manual",
            "document_type": "guía",
            "department": "Operaciones",
            "affected_system": "LogiCore ERP",
            "objective": "Explica cómo revisar pedidos retenidos por reglas manuales de negocio antes de liberarlos al circuito de preparación y expedición.",
            "controls": [
                "verificar dirección, ventana y observaciones del cliente",
                "confirmar que no haya duplicidad de líneas ni bloqueos de crédito ficticios",
                "registrar el motivo de liberación o rechazo en la observación interna",
            ],
            "escalation": "Cualquier retención reiterada del mismo cliente debe revisarse en la reunión diaria de backoffice.",
        },
        {
            "title": "Sincronización de ubicaciones tras reasignación masiva",
            "document_type": "guía",
            "department": "Operaciones",
            "affected_system": "AlmaTrack WMS",
            "objective": "Recoge las verificaciones necesarias tras una reasignación masiva de ubicaciones, especialmente cuando hay oleadas de preparación abiertas.",
            "controls": [
                "comparar el inventario visible en RF y en la consola del supervisor",
                "validar que los movimientos queden reflejados en el histórico interno",
                "bloquear las tareas de picking conflictivas hasta cerrar la revisión",
            ],
            "escalation": "Si la sincronización no converge en dos intentos, debe notificarse al responsable de WMS.",
        },
        {
            "title": "Activación de plan alternativo por caída de integración",
            "document_type": "procedimiento",
            "department": "Operaciones",
            "affected_system": "DocuFlow",
            "objective": "Detalla cómo consultar y ejecutar el procedimiento alternativo cuando la documentación operativa indica una caída temporal de alguna integración interna.",
            "controls": [
                "trabajar siempre sobre la versión vigente del documento publicada en DocuFlow",
                "anotar inicio y fin del modo alternativo con responsables de turno",
                "reconciliar manualmente los movimientos creados durante la contingencia",
            ],
            "escalation": "Toda contingencia superior a treinta minutos debe escalarse a continuidad operativa.",
        },
        {
            "title": "Cierre de expedición con incidencias de última milla",
            "document_type": "procedimiento",
            "department": "Operaciones",
            "affected_system": "RutaNexo",
            "objective": "Resume cómo cerrar una expedición cuando la última milla sufre incidencias, devoluciones parciales o cambios de parada en el mismo turno.",
            "controls": [
                "registrar motivo de cierre excepcional y prueba de entrega interna",
                "notificar al backoffice cualquier parada omitida o reprogramada",
                "actualizar el ETA interno antes de confirmar el cierre del viaje",
            ],
            "escalation": "Los cierres incompletos deben revisarse por el planificador antes del cierre de jornada.",
        },
        {
            "title": "Control operativo de pedidos intercentro",
            "document_type": "guía",
            "department": "Operaciones",
            "affected_system": "LogiCore ERP",
            "objective": "Describe cómo validar pedidos que cruzan más de un centro logístico y requieren mantener coherencia entre reserva, expedición y recepción.",
            "controls": [
                "comprobar origen, tránsito y destino en el mismo hilo operativo",
                "revisar etiquetas, bultos y ventanas comprometidas",
                "confirmar que el pedido no quede visible en estados contradictorios",
            ],
            "escalation": "Cualquier divergencia entre centros debe escalarse al coordinador de red logística.",
        },
        {
            "title": "Alta temporal de visitas técnicas en control de accesos",
            "document_type": "procedimiento",
            "department": "Seguridad",
            "affected_system": "SafeGate",
            "objective": "Define cómo crear credenciales temporales para visitas técnicas sin reutilizar accesos antiguos ni exponer zonas restringidas del centro.",
            "controls": [
                "validar responsable interno, zona permitida y hora de caducidad",
                "registrar documento de identidad y acompañante asignado",
                "revocar la credencial al cerrar la intervención o al terminar el turno",
            ],
            "escalation": "Si la visita requiere ampliar alcance o duración, Seguridad debe aprobarlo de nuevo antes de continuar.",
        },
        {
            "title": "Gestión de permisos especiales para turnos nocturnos",
            "document_type": "guía",
            "department": "Seguridad",
            "affected_system": "SafeGate",
            "objective": "Explica cómo revisar permisos de acceso excepcionales de personal de guardia, mantenimiento o supervisión durante franjas nocturnas.",
            "controls": [
                "verificar que el permiso indique fecha, turno y supervisor",
                "revisar doble factor, grupo de acceso y motivo de excepción",
                "confirmar la retirada del permiso al cerrar el servicio",
            ],
            "escalation": "Los accesos fallidos repetidos deben derivarse a investigación interna de Seguridad.",
        },
        {
            "title": "Bloqueo preventivo ante credenciales compartidas",
            "document_type": "política",
            "department": "Seguridad",
            "affected_system": "SafeGate",
            "objective": "Establece que las credenciales compartidas están prohibidas y que cualquier indicio de uso simultáneo debe bloquearse de forma preventiva.",
            "controls": [
                "documentar la alerta con usuario, hora y zona afectada",
                "notificar al responsable del área antes de reactivar accesos",
                "forzar renovación de credenciales y revisión de permisos asociados",
            ],
            "escalation": "Las reincidencias se tratan como incidente de seguridad y se elevan a la coordinación regional.",
        },
        {
            "title": "Procedimiento de revisión de bitácoras de acceso",
            "document_type": "procedimiento",
            "department": "Seguridad",
            "affected_system": "DocuFlow",
            "objective": "Recoge el método para consultar bitácoras internas de acceso publicadas en DocuFlow y contrastarlas con eventos reportados por el equipo de guardia.",
            "controls": [
                "consultar la versión vigente de la bitácora y no una copia local",
                "anotar discrepancias entre evento observado y evento registrado",
                "cerrar la revisión con observaciones aprobadas por el supervisor",
            ],
            "escalation": "Cuando la bitácora no cuadre con el evento físico, se eleva a investigación interna y soporte funcional.",
        },
        {
            "title": "Custodia de credenciales para proveedores recurrentes",
            "document_type": "política",
            "department": "Seguridad",
            "affected_system": "SafeGate",
            "objective": "Define cómo asignar y custodiar credenciales de proveedores recurrentes sin exponer accesos permanentes ni reutilizar tarjetas caducadas.",
            "controls": [
                "ligar cada credencial a una persona física identificada",
                "revisar la vigencia del contrato o autorización del proveedor",
                "devolver o revocar la credencial al cierre de cada servicio",
            ],
            "escalation": "El incumplimiento de devolución obliga a bloqueo inmediato y revisión del expediente interno.",
        },
        {
            "title": "Respuesta inicial ante alarma de acceso denegado",
            "document_type": "guía",
            "department": "Seguridad",
            "affected_system": "SafeGate",
            "objective": "Describe el protocolo inicial cuando varias personas reportan acceso denegado y existe riesgo de impedir la entrada al turno operativo.",
            "controls": [
                "comprobar vigencia del permiso, zona, horario y factor adicional",
                "registrar incidencia y personas afectadas antes de abrir una excepción",
                "validar con el supervisor si el acceso de contingencia es procedente",
            ],
            "escalation": "Si la alarma afecta a más de un grupo de acceso, se activa coordinación de continuidad.",
        },
        {
            "title": "Alta de conductores y validación documental",
            "document_type": "guía",
            "department": "Onboarding",
            "affected_system": "OnboardHub",
            "objective": "Detalla cómo dar de alta a nuevos conductores, validar licencias, cursos internos y supervisor asignado antes del primer turno.",
            "controls": [
                "comprobar licencia, formación y firma del responsable",
                "asignar supervisor y centro antes de publicar el alta",
                "registrar incidencias documentales en el expediente interno",
            ],
            "escalation": "Si falta documentación crítica, el alta no debe avanzar a la siguiente fase.",
        },
        {
            "title": "Checklist de bienvenida para personal de almacén",
            "document_type": "guía",
            "department": "Onboarding",
            "affected_system": "OnboardHub",
            "objective": "Resume los pasos de bienvenida, permisos, formación y entrega de material para nuevas incorporaciones del área de almacén.",
            "controls": [
                "confirmar acceso, supervisor, calendario y equipo asignado",
                "registrar la lectura de normas de seguridad y operación",
                "validar que el empleado conozca el circuito de soporte y escalado",
            ],
            "escalation": "Las altas incompletas deben bloquearse antes de llegar al primer turno productivo.",
        },
        {
            "title": "Entrega controlada de manuales operativos",
            "document_type": "procedimiento",
            "department": "Onboarding",
            "affected_system": "DocuFlow",
            "objective": "Explica cómo entregar manuales operativos, políticas internas y procedimientos críticos a nuevas incorporaciones usando DocuFlow como fuente única.",
            "controls": [
                "publicar solo la versión aprobada y vigente del manual",
                "registrar confirmación de lectura y fecha de revisión",
                "retirar enlaces obsoletos de materiales de arranque anteriores",
            ],
            "escalation": "Si la persona no puede acceder a la documentación, debe escalarse al responsable de onboarding.",
        },
        {
            "title": "Asignación de permisos iniciales por rol",
            "document_type": "guía",
            "department": "Onboarding",
            "affected_system": "SafeGate",
            "objective": "Define la asignación inicial de permisos físicos para nuevas altas según rol, zona operativa y centro logístico.",
            "controls": [
                "verificar rol, supervisor y centro antes de emitir credenciales",
                "evitar grupos de acceso genéricos o heredados",
                "documentar la expiración prevista si el rol es temporal",
            ],
            "escalation": "Toda desviación frente al perfil estándar debe aprobarla Seguridad antes de entregar la credencial.",
        },
        {
            "title": "Escalado de incidencias críticas con impacto operativo",
            "document_type": "política",
            "department": "Politicas internas",
            "affected_system": "DocuFlow",
            "objective": "Fija cuándo una incidencia pasa a nivel crítico y cómo debe escalarse cuando afecta expediciones, accesos o integraciones de más de un centro.",
            "controls": [
                "medir impacto en pedidos, turnos y continuidad operativa",
                "abrir ticket con prioridad adecuada y responsable visible",
                "mantener actualizaciones temporales hasta cierre o contención",
            ],
            "escalation": "Toda incidencia crítica debe revisarse en la mesa de continuidad y dejar retrospectiva documentada.",
        },
        {
            "title": "Cambios maestros y doble validación",
            "document_type": "política",
            "department": "Politicas internas",
            "affected_system": "LogiCore ERP",
            "objective": "Establece que los cambios en datos maestros con efecto en pedidos recurrentes requieren doble validación antes de publicarse.",
            "controls": [
                "revisar origen de la solicitud y alcance del cambio",
                "validar impacto en facturación, entrega y planificación",
                "registrar quién autoriza y cuándo se ejecuta el cambio",
            ],
            "escalation": "Si el cambio puede afectar rutas ya cerradas, se escala a coordinación operativa antes de aplicarlo.",
        },
    ]
    documents: list[dict] = []
    for index, item in enumerate(templates, start=1):
        created_at = _iso_datetime(index - 1)
        updated_at = _iso_datetime(index - 1, hours=2)
        documents.append(
            {
                "id": index,
                "title": item["title"],
                "document_type": item["document_type"],
                "department": item["department"],
                "affected_system": item["affected_system"],
                "content": _document_content(
                    title=item["title"],
                    department=item["department"],
                    system=item["affected_system"],
                    objective=item["objective"],
                    controls=item["controls"],
                    escalation=item["escalation"],
                ),
                "tags": [
                    item["department"].lower().replace(" ", "-"),
                    item["affected_system"].lower().replace(" ", "-"),
                ],
                "source_url": f"https://intranet.logistica.local/documents/{index:03d}",
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    return [_repair_mojibake(item) for item in documents]


def build_seed_tickets() -> list[dict]:
    resolved_templates = [
        {
            "title": "Acceso temporal rechazado para visita técnica",
            "department": "Seguridad",
            "category": "accesos",
            "system": "SafeGate",
            "site": "el control de accesos del centro norte",
            "process": "alta temporal de visita técnica",
            "symptom": "credenciales válidas marcadas como caducadas al intentar entrar por el torno principal",
            "impact": "El personal de mantenimiento tuvo que esperar autorización manual y se retrasó una intervención planificada.",
            "corrective_action": "se regeneró la credencial temporal con la caducidad correcta y se depuró la regla interna que estaba heredando fechas del día anterior",
            "verification": "Seguridad realizó dos accesos de prueba, confirmó el cierre automático al final del turno y validó que el acompañante interno seguía asociado al registro",
            "priority": "high",
            "tags": ["seguridad", "safegate", "accesos", "resuelto"],
        },
        {
            "title": "Ruta no recalculada tras incidencia vial",
            "department": "Operaciones",
            "category": "rutas",
            "system": "RutaNexo",
            "site": "la mesa de planificación regional",
            "process": "replanificación de ruta con incidencia vial",
            "symptom": "la secuencia seguía mostrando la parada bloqueada y no recalculaba el orden del resto de entregas",
            "impact": "El coordinador no podía publicar una nueva ruta y varios conductores quedaron esperando instrucciones actualizadas.",
            "corrective_action": "se limpiaron restricciones temporales inconsistentes y se reprocesó la ruta desde la cola interna de planificación",
            "verification": "Planificación comparó ETA, ventanas y kilometraje antes de publicar la nueva versión al conductor",
            "priority": "high",
            "tags": ["operaciones", "rutanexo", "rutas", "resuelto"],
        },
        {
            "title": "Diferencia de inventario tras conteo cíclico",
            "department": "Operaciones",
            "category": "almacen",
            "system": "AlmaTrack WMS",
            "site": "el pasillo de preparación del almacén este",
            "process": "conteo cíclico con ubicación en cuarentena",
            "symptom": "la ubicación quedaba bloqueada incluso después de cuadrar el stock físico y el stock reservado",
            "impact": "Se frenó temporalmente la liberación de tareas de picking y aumentó la cola de preparación del turno.",
            "corrective_action": "se recalculó el estado de la ubicación, se reaplicó la confirmación del supervisor y se liberaron las tareas retenidas",
            "verification": "El supervisor repitió el conteo con radiofrecuencia y comprobó que la ubicación volvía a estar disponible",
            "priority": "medium",
            "tags": ["operaciones", "almatrack-wms", "almacen", "resuelto"],
        },
        {
            "title": "Pedido retenido por validación manual",
            "department": "Operaciones",
            "category": "pedidos",
            "system": "LogiCore ERP",
            "site": "el backoffice de expediciones",
            "process": "liberación de pedido retenido",
            "symptom": "el pedido mostraba bloqueo persistente pese a tener todos los datos obligatorios confirmados",
            "impact": "El equipo no podía enviar el pedido a preparación y la promesa de entrega empezaba a degradarse.",
            "corrective_action": "se eliminó una marca residual de revisión manual y se reconstruyó el estado operativo del pedido",
            "verification": "Backoffice validó que el pedido pasó a preparación, generó tareas y quedó visible en el monitor de expediciones",
            "priority": "high",
            "tags": ["operaciones", "logicore-erp", "pedidos", "resuelto"],
        },
        {
            "title": "Checklist de onboarding sin supervisor asignado",
            "department": "Onboarding",
            "category": "onboarding",
            "system": "OnboardHub",
            "site": "la mesa de altas del centro sur",
            "process": "alta inicial de conductor",
            "symptom": "el expediente aparecía incompleto aunque la persona ya tenía licencia y formación registradas",
            "impact": "La incorporación del nuevo conductor quedó bloqueada y no se pudo planificar su primer turno.",
            "corrective_action": "se reasignó el supervisor interno correcto y se reejecutó el flujo de validación del expediente",
            "verification": "Onboarding confirmó que el expediente pasó a estado listo y que el supervisor veía la incorporación en su panel",
            "priority": "medium",
            "tags": ["onboarding", "onboardhub", "altas", "resuelto"],
        },
        {
            "title": "Documento operativo obsoleto publicado en repositorio",
            "department": "Politicas internas",
            "category": "documentacion",
            "system": "DocuFlow",
            "site": "el repositorio de procedimientos internos",
            "process": "consulta de SOP operativa",
            "symptom": "la portada del procedimiento mostraba versión nueva, pero el contenido seguía siendo una edición anterior",
            "impact": "El equipo operativo estaba consultando instrucciones antiguas y existía riesgo de ejecutar pasos ya retirados.",
            "corrective_action": "se republicó la versión correcta, se invalidó la caché interna y se retiraron enlaces antiguos del tablero del turno",
            "verification": "Políticas internas comprobó versión, fecha y firma del aprobador en la copia vigente",
            "priority": "medium",
            "tags": ["politicas-internas", "docuflow", "documentacion", "resuelto"],
        },
        {
            "title": "Permiso físico nocturno sin propagación completa",
            "department": "Seguridad",
            "category": "permisos",
            "system": "SafeGate",
            "site": "el acceso lateral de personal nocturno",
            "process": "activación de permiso especial de turno nocturno",
            "symptom": "el permiso estaba activo en consola pero el lector seguía rechazando al usuario fuera del horario estándar",
            "impact": "El relevo de guardia se retrasó y hubo que gestionar acceso de contingencia.",
            "corrective_action": "se repitió la publicación del grupo de acceso y se actualizó la excepción horaria del turno",
            "verification": "Seguridad confirmó acceso en dos lectores distintos y cierre correcto al terminar el servicio",
            "priority": "high",
            "tags": ["seguridad", "safegate", "permisos", "resuelto"],
        },
        {
            "title": "Sincronización incompleta de ubicación tras reubicación",
            "department": "Operaciones",
            "category": "sincronizacion",
            "system": "AlmaTrack WMS",
            "site": "el área de cross-docking del almacén central",
            "process": "reasignación de ubicación y liberación de tareas",
            "symptom": "la nueva ubicación quedaba visible en consola pero no en los terminales RF del turno",
            "impact": "Los preparadores no podían continuar con normalidad y se acumuló trabajo en la oleada siguiente.",
            "corrective_action": "se forzó la resincronización interna y se regeneró el histórico de movimientos de la ubicación",
            "verification": "El equipo de almacén verificó la visibilidad de la ubicación en RF y en la consola de supervisión",
            "priority": "medium",
            "tags": ["operaciones", "almatrack-wms", "sincronizacion", "resuelto"],
        },
        {
            "title": "Integración de pedido intercentro con estado inconsistente",
            "department": "Operaciones",
            "category": "integracion",
            "system": "LogiCore ERP",
            "site": "el flujo intercentro del hub peninsular",
            "process": "sincronización de pedido entre origen y destino",
            "symptom": "el pedido figuraba expedido en origen pero seguía retenido en el tablero del centro destino",
            "impact": "El equipo de seguimiento no podía confirmar tránsito ni promesa de recepción.",
            "corrective_action": "se recompuso el mensaje interno de sincronización y se volvió a publicar el evento del pedido",
            "verification": "Operaciones revisó que ambos centros vieran el mismo estado final y la misma referencia de envío",
            "priority": "high",
            "tags": ["operaciones", "logicore-erp", "integracion", "resuelto"],
        },
        {
            "title": "Manual de bienvenida sin acuse de lectura",
            "department": "Onboarding",
            "category": "documentacion",
            "system": "DocuFlow",
            "site": "el portal interno de incorporación",
            "process": "entrega de manuales de arranque",
            "symptom": "el documento se abría correctamente pero no registraba la confirmación de lectura del nuevo empleado",
            "impact": "El alta no podía pasar a estado completado y el equipo de onboarding debía hacer seguimiento manual.",
            "corrective_action": "se volvió a publicar el manual con metadatos correctos y se restauró el control de confirmación",
            "verification": "Onboarding abrió el manual con un usuario de prueba y comprobó el registro de lectura en el expediente",
            "priority": "medium",
            "tags": ["onboarding", "docuflow", "documentacion", "resuelto"],
        },
    ]

    open_templates = [
        {
            "title": "Acceso de supervisor rechazado en cambio de turno",
            "department": "Seguridad",
            "category": "accesos",
            "system": "SafeGate",
            "site": "el torno principal del centro oeste",
            "process": "entrada de supervisor al relevo de madrugada",
            "symptom": "el lector muestra acceso denegado de forma intermitente pese a que el permiso parece vigente en consola",
            "impact": "El relevo del turno queda retrasado y el equipo de guardia pierde tiempo confirmando accesos manuales.",
            "priority": "high",
            "tags": ["seguridad", "safegate", "accesos", "abierto"],
        },
        {
            "title": "Ruta congelada tras cambio de parada prioritaria",
            "department": "Operaciones",
            "category": "rutas",
            "system": "RutaNexo",
            "site": "la consola de planificación del centro este",
            "process": "reordenación urgente de parada crítica",
            "symptom": "la secuencia no termina de recalcular y la ruta queda en estado pendiente durante varios minutos",
            "impact": "El conductor no recibe una versión válida y las ventanas de varias entregas empiezan a desviarse.",
            "priority": "high",
            "tags": ["operaciones", "rutanexo", "rutas", "abierto"],
        },
        {
            "title": "Ubicación RF inconsistente después de reposición",
            "department": "Operaciones",
            "category": "sincronizacion",
            "system": "AlmaTrack WMS",
            "site": "la zona de reposición del almacén norte",
            "process": "confirmación de reposición y liberación de picking",
            "symptom": "los terminales RF muestran una ubicación vacía mientras la consola refleja stock disponible",
            "impact": "Las tareas de preparación se frenan y aumenta la cola de pedidos pendientes de salida.",
            "priority": "high",
            "tags": ["operaciones", "almatrack-wms", "sincronizacion", "abierto"],
        },
        {
            "title": "Pedido bloqueado tras doble validación de datos maestros",
            "department": "Operaciones",
            "category": "pedidos",
            "system": "LogiCore ERP",
            "site": "el backoffice del centro metropolitano",
            "process": "liberación de pedido retenido por reglas de cliente",
            "symptom": "el pedido no avanza a preparación aunque la doble validación aparece cerrada en el historial interno",
            "impact": "El equipo comercial y operativo no dispone de una fecha fiable de salida para el cliente afectado.",
            "priority": "high",
            "tags": ["operaciones", "logicore-erp", "pedidos", "abierto"],
        },
        {
            "title": "Expediente de conductor sin cierre de onboarding",
            "department": "Onboarding",
            "category": "onboarding",
            "system": "OnboardHub",
            "site": "la bandeja de altas del equipo de incorporación",
            "process": "cierre de checklist de nuevo conductor",
            "symptom": "el expediente alterna entre pendiente y en revisión sin llegar al estado final de listo para operar",
            "impact": "La incorporación no se puede programar y el equipo dedica tiempo adicional a comprobaciones manuales.",
            "priority": "medium",
            "tags": ["onboarding", "onboardhub", "altas", "abierto"],
        },
        {
            "title": "Permiso temporal no revocado al terminar el servicio",
            "department": "Seguridad",
            "category": "permisos",
            "system": "SafeGate",
            "site": "el acceso de proveedores del centro logístico sur",
            "process": "cierre de credencial temporal de proveedor",
            "symptom": "la credencial sigue apareciendo como válida después de la hora prevista de expiración",
            "impact": "Existe riesgo operativo de acceso no controlado y el equipo de seguridad debe revisar el caso manualmente.",
            "priority": "critical",
            "tags": ["seguridad", "safegate", "permisos", "abierto"],
        },
        {
            "title": "Guía operativa publicada con enlaces internos rotos",
            "department": "Politicas internas",
            "category": "documentacion",
            "system": "DocuFlow",
            "site": "el repositorio central de procedimientos",
            "process": "consulta de guía operativa por el turno de tarde",
            "symptom": "los enlaces a anexos y controles obligatorios devuelven contenido no disponible",
            "impact": "El equipo no puede completar el procedimiento con seguridad y empieza a usar referencias manuales antiguas.",
            "priority": "medium",
            "tags": ["politicas-internas", "docuflow", "documentacion", "abierto"],
        },
        {
            "title": "Mensaje de sincronización intercentro no consumido",
            "department": "Operaciones",
            "category": "integracion",
            "system": "LogiCore ERP",
            "site": "la cola de integración del hub nacional",
            "process": "actualización de pedido entre centros logísticos",
            "symptom": "el evento permanece en estado pendiente y el destino no refleja la actualización de estado",
            "impact": "Se pierde visibilidad operativa del movimiento intercentro y aumenta el trabajo de seguimiento manual.",
            "priority": "high",
            "tags": ["operaciones", "logicore-erp", "integracion", "abierto"],
        },
        {
            "title": "Recalculo masivo pendiente tras incidencia meteorológica",
            "department": "Operaciones",
            "category": "rutas",
            "system": "RutaNexo",
            "site": "la torre de control de distribución",
            "process": "replanificación masiva por meteorología adversa",
            "symptom": "varias rutas quedan en cola de cálculo y ninguna publica una secuencia final usable",
            "impact": "La salida de vehículos del primer turno se retrasa y se degrada la promesa de entrega.",
            "priority": "critical",
            "tags": ["operaciones", "rutanexo", "rutas", "abierto"],
        },
        {
            "title": "Confirmación de lectura ausente en manual de arranque",
            "department": "Onboarding",
            "category": "documentacion",
            "system": "DocuFlow",
            "site": "el portal interno de incorporación de almacén",
            "process": "firma de lectura de documentación inicial",
            "symptom": "el sistema permite abrir el manual pero no registra la aceptación final del empleado",
            "impact": "El expediente queda abierto y el equipo de onboarding no puede cerrar el proceso de incorporación.",
            "priority": "medium",
            "tags": ["onboarding", "docuflow", "documentacion", "abierto"],
        },
    ]

    tickets: list[dict] = []
    identifier = 1
    for cycle in range(9):
        for template in resolved_templates:
            tags = template["tags"] + [f"ciclo-{cycle + 1}"]
            tickets.append(
                _resolved_ticket_payload(
                    identifier=identifier,
                    title=template["title"],
                    department=template["department"],
                    category=template["category"],
                    system=template["system"],
                    site=template["site"],
                    process=template["process"],
                    symptom=template["symptom"],
                    impact=template["impact"],
                    corrective_action=template["corrective_action"],
                    verification=template["verification"],
                    priority=template["priority"],
                    tags=tags,
                )
            )
            identifier += 1

    for template in open_templates:
        tickets.append(
            _open_ticket_payload(
                identifier=identifier,
                title=template["title"],
                department=template["department"],
                category=template["category"],
                system=template["system"],
                site=template["site"],
                process=template["process"],
                symptom=template["symptom"],
                impact=template["impact"],
                priority=template["priority"],
                tags=template["tags"],
            )
        )
        identifier += 1

    return [_repair_mojibake(item) for item in tickets]


def write_seed_files(data_dir: Path = DATA_DIR) -> tuple[Path, Path]:
    tickets = build_seed_tickets()
    documents = build_seed_documents()
    data_dir.mkdir(parents=True, exist_ok=True)
    tickets_path = data_dir / TICKETS_FILE
    documents_path = data_dir / DOCUMENTS_FILE
    tickets_path.write_text(json.dumps(tickets, ensure_ascii=False, indent=2), encoding="utf-8")
    documents_path.write_text(json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8")
    return tickets_path, documents_path


def load_seed_data(data_dir: Path = DATA_DIR) -> tuple[list[dict], list[dict]]:
    tickets_path = data_dir / TICKETS_FILE
    documents_path = data_dir / DOCUMENTS_FILE
    try:
        tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeedDataValidationError(f"No existe el archivo requerido: {tickets_path}") from exc
    except json.JSONDecodeError as exc:
        raise SeedDataValidationError(f"JSON inválido en {tickets_path}: {exc}") from exc

    try:
        documents = json.loads(documents_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SeedDataValidationError(f"No existe el archivo requerido: {documents_path}") from exc
    except json.JSONDecodeError as exc:
        raise SeedDataValidationError(f"JSON inválido en {documents_path}: {exc}") from exc

    if not isinstance(tickets, list):
        raise SeedDataValidationError("El dataset de tickets debe ser una lista JSON")
    if not isinstance(documents, list):
        raise SeedDataValidationError("El dataset de documentos debe ser una lista JSON")
    return _repair_mojibake(tickets), _repair_mojibake(documents)


def _parse_iso8601(value: str, *, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SeedDataValidationError(f"Fecha inválida en {field_name}: {value}") from exc


def _require_fields(item: dict, *, field_names: list[str], item_label: str) -> None:
    missing = [field_name for field_name in field_names if field_name not in item or item[field_name] in (None, "", [])]
    if missing:
        raise SeedDataValidationError(f"{item_label} no contiene los campos obligatorios: {', '.join(missing)}")


def _validate_unique(items: list[dict], *, field_name: str, item_type: str) -> None:
    seen: set = set()
    duplicates: list[str] = []
    for item in items:
        value = item.get(field_name)
        if value in (None, ""):
            continue
        if value in seen:
            duplicates.append(str(value))
            continue
        seen.add(value)
    if duplicates:
        raise SeedDataValidationError(f"Hay {item_type} con {field_name} duplicado: {', '.join(sorted(duplicates))}")


def validate_seed_data(tickets: list[dict], documents: list[dict]) -> SeedDataSummary:
    if len(tickets) != 100:
        raise SeedDataValidationError(f"Se esperaban 100 tickets y se encontraron {len(tickets)}")
    if len(documents) != 20:
        raise SeedDataValidationError(f"Se esperaban 20 documentos y se encontraron {len(documents)}")

    resolved_tickets = sum(1 for item in tickets if item.get("is_resolved") is True)
    unresolved_tickets = sum(1 for item in tickets if item.get("is_resolved") is False)
    if resolved_tickets != 90:
        raise SeedDataValidationError(f"Se esperaban 90 tickets resueltos y se encontraron {resolved_tickets}")
    if unresolved_tickets != 10:
        raise SeedDataValidationError(f"Se esperaban 10 tickets no resueltos y se encontraron {unresolved_tickets}")

    _validate_unique(tickets, field_name="id", item_type="tickets")
    _validate_unique(tickets, field_name="external_id", item_type="tickets")
    _validate_unique(documents, field_name="id", item_type="documentos")

    ticket_fields = [
        "title",
        "description",
        "department",
        "category",
        "affected_system",
        "priority",
        "status",
        "is_resolved",
        "created_at",
        "tags",
    ]
    document_fields = [
        "title",
        "document_type",
        "department",
        "affected_system",
        "content",
        "tags",
        "created_at",
        "updated_at",
    ]

    for index, ticket in enumerate(tickets, start=1):
        item_label = f"Ticket #{index}"
        if "id" not in ticket or "external_id" not in ticket or not ticket["external_id"]:
            raise SeedDataValidationError(f"{item_label} debe contener id y external_id")
        _require_fields(ticket, field_names=ticket_fields, item_label=item_label)
        if ticket["affected_system"] not in ALLOWED_SYSTEMS:
            raise SeedDataValidationError(f"{item_label} contiene un affected_system no permitido: {ticket['affected_system']}")
        if not isinstance(ticket["tags"], list) or not ticket["tags"]:
            raise SeedDataValidationError(f"{item_label} debe contener al menos una tag")
        _parse_iso8601(str(ticket["created_at"]), field_name=f"{item_label}.created_at")
        if "updated_at" in ticket and ticket["updated_at"]:
            _parse_iso8601(str(ticket["updated_at"]), field_name=f"{item_label}.updated_at")

        if ticket["is_resolved"] is True:
            _require_fields(ticket, field_names=["resolution", "resolved_at"], item_label=item_label)
            _parse_iso8601(str(ticket["resolved_at"]), field_name=f"{item_label}.resolved_at")
            if str(ticket["status"]).lower() != "resolved":
                raise SeedDataValidationError(f"{item_label} debe tener status=resolved cuando is_resolved=true")
        elif ticket["is_resolved"] is False:
            if ticket.get("resolution") not in (None, ""):
                raise SeedDataValidationError(f"{item_label} no resuelto no debe contener resolution")
            if ticket.get("resolved_at") not in (None, ""):
                raise SeedDataValidationError(f"{item_label} no resuelto no debe contener resolved_at")
            _require_fields(ticket, field_names=["impact", "expected_behavior", "actual_behavior"], item_label=item_label)
        else:
            raise SeedDataValidationError(f"{item_label} debe tener is_resolved=true o false")

    for index, document in enumerate(documents, start=1):
        item_label = f"Documento #{index}"
        if "id" not in document:
            raise SeedDataValidationError(f"{item_label} debe contener un id estable")
        _require_fields(document, field_names=document_fields, item_label=item_label)
        if document["affected_system"] not in ALLOWED_SYSTEMS:
            raise SeedDataValidationError(f"{item_label} contiene un affected_system no permitido: {document['affected_system']}")
        if len(document["content"]) < 500:
            raise SeedDataValidationError(f"{item_label} debe tener al menos 500 caracteres de contenido")
        if not isinstance(document["tags"], list) or not document["tags"]:
            raise SeedDataValidationError(f"{item_label} debe contener al menos una tag")
        _parse_iso8601(str(document["created_at"]), field_name=f"{item_label}.created_at")
        _parse_iso8601(str(document["updated_at"]), field_name=f"{item_label}.updated_at")

    document_distribution: dict[str, int] = {}
    for item in documents:
        document_distribution[item["department"]] = document_distribution.get(item["department"], 0) + 1
    if document_distribution != ALLOWED_DOCUMENT_DISTRIBUTION:
        raise SeedDataValidationError(
            "La distribución de documentos es inválida: "
            f"se esperaba {ALLOWED_DOCUMENT_DISTRIBUTION} y se obtuvo {document_distribution}"
        )

    return SeedDataSummary(
        tickets=len(tickets),
        resolved_tickets=resolved_tickets,
        unresolved_tickets=unresolved_tickets,
        documents=len(documents),
        document_distribution=document_distribution,
    )


def validate_seed_files(data_dir: Path = DATA_DIR) -> SeedDataSummary:
    tickets, documents = load_seed_data(data_dir)
    return validate_seed_data(tickets, documents)


# The checked-in JSON files are the canonical synthetic corpus.  These helper
# names are kept for tests and tooling compatibility without regenerating the
# older templated corpus.
def build_seed_tickets() -> list[dict]:
    tickets, _ = load_seed_data(DATA_DIR)
    return tickets


def build_seed_documents() -> list[dict]:
    _, documents = load_seed_data(DATA_DIR)
    return documents
