const EN = {
  loading: "Loading…",
  save: "Save",
  saved: "Saved",
  savedNotSent: "Saved, but your agent does not know it yet",
  saveFailed: "Save failed: {detail}",
  cancel: "Cancel",
  close: "Close",
  copy: "Copy",
  copied: "Copied",
  copyManually: "Selected — press Ctrl+C",
  next: "Next",
  back: "Back",
  skip: "Skip",
  timeoutNotBelowAgent:
    "The confirmation timeout must be lower than the agent's response timeout.",

  statusConnected: "Agent connected",
  statusDisconnected: "Agent not connected",
  statusDisconnectedDetail: "ElevenLabs could not be reached.",
  checkTitle: "Connection check",
  checkRunning: "Checking…",
  checkAllOk: "Everything works.",
  checkProblems: "Something needs attention.",
  checkFailed: "The check could not run: {detail}",
  check_loaded: "Integration loaded",
  check_key: "API key and agent readable",
  check_protected: "Agent protected",
  check_tools_attached: "Both tools attached to the agent",
  check_tools_current: "Tools up to date",
  check_webhook: "Webhook reachable from the internet",
  check_timeouts: "Confirmation wait below the agent's",
  check_missing_entities: "No missing entities",
  checkSkipped: "Not checked: the agent could not be read.",
  checkKeyRefused: "ElevenLabs refused the key.",
  checkKeyScope: "The key lacks a scope the agent read needs.",
  checkKeyAgentNotFound: "The agent could not be found.",
  checkKeyUnreachable: "ElevenLabs could not be reached.",
  checkNoExternalUrl: "Home Assistant has no external URL configured.",
  checkProtectedDetail: "Anyone who knows the agent's id can talk to it.",
  checkTimeoutsDetail: "{confirm} s here, {agent} s in the agent.",
  checkLoadedDetail: "State: {detail}",
  checkToolsMissing: "Not on the agent: {list}.",
  checkToolsExtra: "On the agent but no longer selected: {list}.",
  checkToolsUnreadable: "Could not read: {list}.",
  checkToolsOther: "Differs from what a sync sends: {list}.",
  treeNotSynced: "Not synced",
  treeNotSyncedHint: "Save or run a sync to send it.",

  sectionAgent: "Agent",
  sectionEntities: "Entities",

  navPrompt: "Prompt",
  navPromptHint: "Your prompt beside the one we recommend",
  navSettings: "Agent settings",
  navSettingsHint: "Opening line, confirmation timeout, speech before acting",
  navRotations: "Rotations",
  navRotationsHint: "Replace the shared secret or the webhook URL",
  navRead: "Read",
  navReadHint: "Entities the agent may read",
  navControl: "Control",
  navControlHint: "Entities the agent may act on",
  navExceptions: "Restrictions",
  navExceptionsHint: "Actions removed from a controlled entity",
  navAutoExpose: "Automatic exposure",
  navAutoExposeHint: "What happens to entities you add later",
  navMissing: "Missing entities",
  navMissingHint: "Permissions whose entity no longer exists",
  missingSubtitle:
    "These entities are not in Home Assistant now. Remove the ones you deleted. " +
    "An entity that is only reloading comes back by itself.",
  missingRead: "Read",
  missingControl: "Control",
  missingRestricted: "Restrictions",
  missingUnknown: "Deleted entity",
  missingRemove: "Remove",
  treeNoUniqueId: "No unique_id",
  treeNoUniqueIdHint:
    "Renaming this entity removes its permission, and another entity that takes " +
    "the name would receive it. Add a unique_id to its YAML to fix this.",

  promptHeading: "Agent prompt",
  promptHint: "Copy what you want from the right into your own prompt.",
  promptYours: "Your prompt",
  promptRecommended: "What we recommend",
  promptBlockPresent: "Your prompt already carries the recommended block.",
  promptBlockMissing: "Your prompt does not carry the recommended block yet.",
  promptBlockTwice:
    "Your prompt carries the recommended block more than once. Keep one copy.",
  promptBlockOutdated:
    "Your prompt carries an older version of the recommended block. Replace it with the one on the right.",

  treeSearch: "Search entities",
  treeSelected: "{count} selected",
  treeClear: "Clear",
  treeNoArea: "No area",
  treeEmpty: "Nothing matches that search.",

  settingsHeading: "Agent settings",
  settingsSubtitle: "How the agent opens, waits and announces itself.",
  firstMessageLabel: "Opening line",
  firstMessageHint: "The first thing the agent says. Leave it empty to let the caller speak first.",
  firstMessageOffline: "Available when ElevenLabs can be reached.",
  confirmTimeoutLabel: "Confirmation timeout (seconds)",
  confirmTimeoutDescription:
    "How long the integration waits for Home Assistant to finish an action " +
    "before it answers the agent. When it runs out the agent says it cannot " +
    "confirm the result; the action itself still runs to the end.",
  confirmTimeoutHint:
    "Must stay below the agent's own response timeout, currently {timeout} s.",
  eventsCooldownLabel: "Event cooldown (minutes)",
  eventsCooldownHint: "The same event is not sent again within this time. High priority skips it.",
  eventsLimitLabel: "Events per hour",
  eventsLimitHint: "A cap on events sent to the agent. Each one spends ElevenLabs credits.",
  eventsContinuityLabel: "Event memory (minutes)",
  eventsContinuityHint: "Recent events travel with the next one within this time. 0 sends each event alone.",
  eventsOutOfRange: "Cooldown and memory go from 0 to 1440 minutes; events per hour from 1 to 1000.",
  preToolSpeechLabel: "Speech before tool calls",
  preToolSpeechHint: "Whether the agent says something before it acts.",
  preToolSpeechAuto: "Auto",
  preToolSpeechForce: "Always",
  preToolSpeechOff: "Never",

  exposureHeading: "Automatic exposure",
  exposureSubtitle: "What happens to entities you add to Home Assistant later.",
  autoExposeAreasLabel: "Watched areas",
  autoExposeAreasHint:
    "New entities that appear in these areas join the lists on their own. " +
    "With no area picked, nothing is added.",
  autoExposeLabel: "Let them be read",
  autoControlLabel: "Let them be controlled",

  operationsHeading: "Rotations",
  operationsDescription: "These take effect immediately and cannot be undone.",
  rotateSecretLabel: "Rotate shared secret",
  rotateSecretConfirm:
    "This invalidates the current shared secret immediately, with no grace " +
    "period. Continue?",
  rotateWebhookLabel: "Rotate webhook URL",
  rotateWebhookConfirm:
    "This retires the current webhook URL and replaces it with a new one. " +
    "Continue?",
  rotateSuccess: "Done.",
  rotateFailed: "Rotation failed: {detail}",

  actionsEmpty: "No controlled entities yet.",
  actionsRestrictedCount: "{restricted} of {total} restricted",
  actionsNoActions: "This entity currently offers no actions to restrict.",
  sensitiveLabel: "Sensitive",
  sensitiveConfirm: "Ask for confirmation in a conversation",
  sensitiveRefuse: "Refuse in automatic runs (events)",
  sensitiveUnprotected: "Marked as sensitive, but neither option protects it.",

  wizardProgress: "Step {step} of {total}",
  wizardStepRead: "What the agent may read",
  wizardReadSubtitle: "Choose the entities the agent is allowed to read the state of.",
  wizardStepControl: "What it may control",
  wizardControlSubtitle: "Choose the entities the agent is allowed to control.",
  wizardStepActions: "Actions to remove",
  wizardActionsSubtitle:
    "Optionally remove specific actions from an entity's granted permissions.",
};

const ES = {
  loading: "Cargando…",
  save: "Guardar",
  saved: "Guardado",
  savedNotSent: "Guardado, pero tu agente aún no lo sabe",
  saveFailed: "Error al guardar: {detail}",
  cancel: "Cancelar",
  close: "Cerrar",
  copy: "Copiar",
  copied: "Copiado",
  copyManually: "Seleccionado — pulsa Ctrl+C",
  next: "Siguiente",
  back: "Atrás",
  skip: "Omitir",
  timeoutNotBelowAgent:
    "El tiempo de confirmación debe ser menor que el tiempo de respuesta del agente.",

  statusConnected: "Agente conectado",
  statusDisconnected: "Agente sin conexión",
  statusDisconnectedDetail: "No se ha podido contactar con ElevenLabs.",
  checkTitle: "Comprobación de la conexión",
  checkRunning: "Comprobando…",
  checkAllOk: "Todo funciona.",
  checkProblems: "Hay algo que revisar.",
  checkFailed: "No se ha podido comprobar: {detail}",
  check_loaded: "Integración cargada",
  check_key: "Clave de API y agente accesibles",
  check_protected: "Agente protegido",
  check_tools_attached: "Las dos herramientas conectadas al agente",
  check_tools_current: "Herramientas al día",
  check_webhook: "Webhook accesible desde internet",
  check_timeouts: "Espera de confirmación menor que la del agente",
  check_missing_entities: "Ninguna entidad desaparecida",
  checkSkipped: "Sin comprobar: no se ha podido leer el agente.",
  checkKeyRefused: "ElevenLabs ha rechazado la clave.",
  checkKeyScope: "A la clave le falta un permiso que necesita la lectura del agente.",
  checkKeyAgentNotFound: "No se ha encontrado el agente.",
  checkKeyUnreachable: "No se ha podido contactar con ElevenLabs.",
  checkNoExternalUrl: "Home Assistant no tiene configurada una URL externa.",
  checkProtectedDetail: "Cualquiera que conozca el id del agente puede hablar con él.",
  checkTimeoutsDetail: "{confirm} s aquí, {agent} s en el agente.",
  checkLoadedDetail: "Estado: {detail}",
  checkToolsMissing: "No está en el agente: {list}.",
  checkToolsExtra: "Sigue en el agente aunque ya no está seleccionada: {list}.",
  checkToolsUnreadable: "No se ha podido leer: {list}.",
  checkToolsOther: "Difiere de lo que envía una sincronización: {list}.",
  treeNotSynced: "Sin sincronizar",
  treeNotSyncedHint: "Guarda o sincroniza para enviarla.",

  sectionAgent: "Agente",
  sectionEntities: "Entidades",

  navPrompt: "Prompt",
  navPromptHint: "Tu prompt junto al que recomendamos",
  navSettings: "Configuración del agente",
  navSettingsHint: "Primer mensaje, tiempo de confirmación, voz antes de actuar",
  navRotations: "Rotaciones",
  navRotationsHint: "Sustituir el secreto compartido o la URL del webhook",
  navRead: "Mostrar",
  navReadHint: "Entidades que el agente puede leer",
  navControl: "Controlar",
  navControlHint: "Entidades sobre las que el agente puede actuar",
  navExceptions: "Restricciones",
  navExceptionsHint: "Acciones retiradas a una entidad controlada",
  navAutoExpose: "Exposición automática",
  navAutoExposeHint: "Qué pasa con las entidades que añadas más adelante",
  navMissing: "Entidades desaparecidas",
  navMissingHint: "Permisos cuya entidad ya no existe",
  missingSubtitle:
    "Estas entidades no están ahora en Home Assistant. Quita las que hayas borrado. " +
    "Una entidad que solo se está recargando vuelve sola.",
  missingRead: "Lectura",
  missingControl: "Control",
  missingRestricted: "Restricciones",
  missingUnknown: "Entidad borrada",
  missingRemove: "Eliminar",
  treeNoUniqueId: "Sin unique_id",
  treeNoUniqueIdHint:
    "Si renombras esta entidad pierde su permiso, y otra que tome el nombre lo " +
    "recibiría. Añade un unique_id en su YAML para evitarlo.",

  promptHeading: "Prompt del agente",
  promptHint: "Copia lo que quieras de la derecha a tu propio prompt.",
  promptYours: "Tu prompt",
  promptRecommended: "El que recomendamos",
  promptBlockPresent: "Tu prompt ya lleva el bloque recomendado.",
  promptBlockMissing: "Tu prompt todavía no lleva el bloque recomendado.",
  promptBlockTwice:
    "Tu prompt lleva el bloque recomendado más de una vez. Deja una sola copia.",
  promptBlockOutdated:
    "Tu prompt lleva una versión antigua del bloque recomendado. Sustitúyela por la de la derecha.",

  treeSearch: "Buscar entidades",
  treeSelected: "{count} seleccionadas",
  treeClear: "Limpiar",
  treeNoArea: "Sin área",
  treeEmpty: "No hay nada que coincida con esa búsqueda.",

  settingsHeading: "Configuración del agente",
  settingsSubtitle: "Cómo abre la conversación, cuánto espera y si se anuncia.",
  firstMessageLabel: "Primer mensaje",
  firstMessageHint:
    "Lo primero que dice el agente. Déjalo vacío para que hable antes quien llama.",
  firstMessageOffline: "Disponible cuando se pueda contactar con ElevenLabs.",
  confirmTimeoutLabel: "Tiempo de confirmación (segundos)",
  confirmTimeoutDescription:
    "Cuánto espera la integración a que Home Assistant termine una acción " +
    "antes de responderle al agente. Si se agota, el agente dice que no puede " +
    "confirmar el resultado; la acción sigue hasta el final igualmente.",
  confirmTimeoutHint:
    "Debe ser menor que el tiempo de respuesta del agente, actualmente {timeout} s.",
  eventsCooldownLabel: "Cooldown de eventos (minutos)",
  eventsCooldownHint: "El mismo evento no se vuelve a enviar dentro de este tiempo. La prioridad alta se lo salta.",
  eventsLimitLabel: "Eventos por hora",
  eventsLimitHint: "Un tope de eventos enviados al agente. Cada uno gasta créditos de ElevenLabs.",
  eventsContinuityLabel: "Memoria de eventos (minutos)",
  eventsContinuityHint: "Los eventos recientes viajan con el siguiente dentro de este tiempo. 0 envía cada evento solo.",
  eventsOutOfRange: "El cooldown y la memoria van de 0 a 1440 minutos; los eventos por hora, de 1 a 1000.",
  preToolSpeechLabel: "Voz antes de usar herramientas",
  preToolSpeechHint: "Si el agente dice algo antes de actuar.",
  preToolSpeechAuto: "Automático",
  preToolSpeechForce: "Siempre",
  preToolSpeechOff: "Nunca",

  exposureHeading: "Exposición automática",
  exposureSubtitle:
    "Qué pasa con las entidades que añadas a Home Assistant más adelante.",
  autoExposeAreasLabel: "Áreas vigiladas",
  autoExposeAreasHint:
    "Las entidades nuevas que aparezcan en estas áreas entran solas en las " +
    "listas. Sin ningún área elegida no se añade nada.",
  autoExposeLabel: "Dejar que se lean",
  autoControlLabel: "Dejar que se controlen",

  operationsHeading: "Rotaciones",
  operationsDescription:
    "Estas acciones surten efecto de inmediato y no se pueden deshacer.",
  rotateSecretLabel: "Rotar secreto compartido",
  rotateSecretConfirm:
    "Esto invalida el secreto compartido actual de inmediato, sin periodo de " +
    "gracia. ¿Continuar?",
  rotateWebhookLabel: "Rotar URL del webhook",
  rotateWebhookConfirm:
    "Esto retira la URL del webhook actual y la sustituye por una nueva. " +
    "¿Continuar?",
  rotateSuccess: "Hecho.",
  rotateFailed: "La rotación falló: {detail}",

  actionsEmpty: "Todavía no hay entidades controladas.",
  actionsRestrictedCount: "{restricted} de {total} restringidas",
  actionsNoActions: "Esta entidad no ofrece ninguna acción para restringir.",
  sensitiveLabel: "Delicado",
  sensitiveConfirm: "Pedir confirmación en una conversación",
  sensitiveRefuse: "Rechazar en ejecuciones automáticas (eventos)",
  sensitiveUnprotected: "Marcado como delicado, pero ninguna opción lo protege.",

  wizardProgress: "Paso {step} de {total}",
  wizardStepRead: "Qué puede leer el agente",
  wizardReadSubtitle: "Elige las entidades cuyo estado puede leer el agente.",
  wizardStepControl: "Qué puede controlar",
  wizardControlSubtitle: "Elige las entidades que puede controlar el agente.",
  wizardStepActions: "Acciones a eliminar",
  wizardActionsSubtitle:
    "Opcionalmente, elimina acciones concretas de los permisos concedidos " +
    "a una entidad.",
};

export function strings(language) {
  return (language || "").startsWith("es") ? { ...EN, ...ES } : EN;
}
