# Read-only, didactic catalog of the Session 13 *live* multi-agent graph
# (`ai-service/app/domain/graph/agents/`). It is the single source of truth for
# both the "Agentes del grafo" section of the Agentes console and the flow
# resource screen (`agents/graph_flow`).
#
# Deliberately static prose (like the ACB read-only card): the service IA is not
# consulted, so the screens render even when it is down. The models shown are the
# `.env` defaults of each knob in `ai-service/app/config.py` — these graph knobs
# are `.env`-only (not runtime-overridable via Ajustes), so the knob name is
# surfaced as documentation, not as an editable field. Nothing here changes the
# flow; it only describes it.
module Agents
  class GraphFlow
    # One node of the graph, in flow order.
    # - kind:  :agent | :gate | :fanout | :join   (drives the badge + styling)
    # - edge:  the transition that LEAVES this node, for the diagram connectors
    #          :handover (Command(goto=…)) | :edge (static) | :send (Send fan-out)
    #          :join (fan-in of the N branches) | :conditional (proposal | END) | :end
    Node = Struct.new(
      :key, :label, :node_fn, :kind, :model, :config_key, :role, :explanation, :edge,
      keyword_init: true
    )

    NODES = [
      Node.new(
        key: "classifier",
        label: "Classifier",
        node_fn: "classifier_agent",
        kind: :agent,
        model: "gpt-4o-mini",
        config_key: "GRAPH_CLASSIFIER_MODEL",
        role: "Clasifica la complejidad y reformula el brief.",
        explanation: "Lee la transcripción cruda y produce una ComplexityClassification: " \
                     "una etiqueta de complejidad (low / medium / high) y un brief reformulado y " \
                     "limpio. La complejidad decide luego el reasoning effort del agente de " \
                     "estructura. Es el nodo de entrada y hace el primer handover " \
                     "(Command(goto) → structure), sin arista estática.",
        edge: :handover
      ),
      Node.new(
        key: "structure",
        label: "Structure",
        node_fn: "structure_agent",
        kind: :agent,
        model: "gpt-5",
        config_key: "AGENT_MODEL",
        role: "Descompone el brief en módulos → tareas (sin horas).",
        explanation: "Reutiliza el agente hecho a mano (bucle sobre la Responses API) para convertir " \
                     "el brief en un árbol de módulos y tareas, todavía sin horas. El esfuerzo de " \
                     "razonamiento se elige según la complejidad del classifier " \
                     "(GRAPH_STRUCTURE_EFFORT_BY_COMPLEXITY).",
        edge: :edge
      ),
      Node.new(
        key: "gate_structure",
        label: "🧑 Gate 1 · revisión de estructura",
        node_fn: "human_gate_structure",
        kind: :gate,
        model: nil,
        config_key: nil,
        role: "Pausa para revisión humana de los módulos y tareas.",
        explanation: "Human-in-the-loop: interrupt() detiene el grafo y expone la estructura para que " \
                     "una persona la edite o apruebe. Al reanudar (Command(resume=…)) devuelve los " \
                     "módulos aprobados. Disciplina interrupt-first: no escribe en el estado antes de " \
                     "pausar, porque el resume re-ejecuta el nodo entero.",
        edge: :send
      ),
      Node.new(
        key: "hours",
        label: "Hours ×N",
        node_fn: "estimate_task_hours",
        kind: :fanout,
        model: nil,
        config_key: "TASK_HOURS_TOP_K / TASK_HOURS_DISTANCE_THRESHOLD",
        role: "Horas por tarea vía búsqueda vectorial determinista.",
        explanation: "No usa LLM: es un fan-out con la Send API (una rama en paralelo por cada tarea " \
                     "aprobada). Cada rama busca las tareas históricas más cercanas y deriva las horas " \
                     "por consenso ponderado por distancia. Sin análogo " \
                     "por debajo del umbral → tarea marcada en rojo.",
        edge: :join
      ),
      Node.new(
        key: "recover",
        label: "Recover & handover",
        node_fn: "recover_and_handover",
        kind: :join,
        model: "gpt-5",
        config_key: "AGENT_MODEL",
        role: "Junta las ramas, recupera tareas dudosas y construye la estimación.",
        explanation: "Nodo de join del fan-out. Detecta tareas dudosas (sin match, rango contradictorio " \
                     "o fiabilidad baja) y, si las hay, lanza un bucle agéntico de recuperación. " \
                     "Fusiona las horas y construye el Estimate consolidado. Termina con el segundo " \
                     "handover (Command(goto) → analysis).",
        edge: :handover
      ),
      Node.new(
        key: "analysis",
        label: "Analysis",
        node_fn: "analysis_agent",
        kind: :agent,
        model: "gpt-4o",
        config_key: "GRAPH_ANALYSIS_MODEL",
        role: "Redacta el informe de fiabilidad (no toca los números).",
        explanation: "Lee la estimación y escribe un ReliabilityReport: confianza global, ratio de tareas " \
                     "fundamentadas (calculado de forma determinista, sobrescribe el del LLM), puntos " \
                     "débiles y un resumen. No modifica ninguna cifra; solo evalúa.",
        edge: :edge
      ),
      Node.new(
        key: "gate_analysis",
        label: "🧑 Gate 2 · validación final",
        node_fn: "human_gate_analysis",
        kind: :gate,
        model: nil,
        config_key: nil,
        role: "Pausa para la validación humana del informe y la estimación.",
        explanation: "Segundo human-in-the-loop: interrupt() expone la estimación y el informe de " \
                     "fiabilidad. Al reanudar, aplica los overrides, fija el status (validated / " \
                     "needs_review) y registra si se pidió una propuesta comercial (want_proposal).",
        edge: :conditional
      ),
      Node.new(
        key: "proposal",
        label: "Proposal",
        node_fn: "proposal_agent",
        kind: :agent,
        model: "gpt-4o",
        config_key: "GRAPH_PROPOSAL_MODEL",
        role: "Bonus: redacta la propuesta comercial.",
        explanation: "Nodo condicional: solo se ejecuta si GRAPH_PROPOSAL_ENABLED está activo Y el humano " \
                     "pidió propuesta en el gate 2. Redacta una CommercialProposal en markdown, anclada " \
                     "estrictamente a la estimación validada. En caso contrario, el grafo termina en END.",
        edge: :end
      )
    ].freeze

    # Badge label per node kind (for the read-only table + the diagram).
    KIND_LABELS = {
      agent: "agente",
      gate: "gate humano",
      fanout: "fan-out",
      join: "join"
    }.freeze

    def self.kind_label(kind) = KIND_LABELS.fetch(kind, kind.to_s)

    # --- Matrix cast (Session 13 live, didactic) -----------------------------
    # Each step is "played" by a Matrix character. Neo is the structure agent (the
    # S12 hand-written agent). The service IA injects the matching persona into each
    # agent's prompt (app/domain/graph/personas.py) — keep the cast in sync.
    #
    # ``persona`` is the short English framing string the S12 RAG wizard sends to the
    # agent endpoints (POST /v1/estimate/agent/{structure,hours}) so each step runs as
    # its own agent — Neo drafts the structure, Trinity recovers the doubtful hours. It
    # mirrors the graph-side text in ai-service/app/domain/graph/personas.py and always
    # ends with a guardrail so the character never breaks the required output shape.
    Character = Struct.new(:name, :tagline, :avatar, :classes, :persona, keyword_init: true)

    _guardrail = " Stay fully professional, accurate and concise; never sacrifice " \
                 "correctness or the required output structure for the character."

    _operator = Character.new(
      name: "El Operador", tagline: "Tú tomas la decisión", avatar: "🧑",
      classes: "bg-white/10 text-white/70 border-white/20"
    )
    CHARACTERS = {
      "classifier" => Character.new(
        name: "Morpheus", tagline: "Te mostraré hasta dónde llega la madriguera", avatar: "🕶",
        classes: "bg-emerald-500/20 text-emerald-300 border-emerald-400/40"
      ),
      "structure" => Character.new(
        name: "Neo", tagline: "Yo sé kung-fu — veo la estructura", avatar: "🥋",
        classes: "bg-amber-500/20 text-amber-300 border-amber-400/40",
        persona: "You are Neo: you see the underlying structure of the system with total " \
                 "clarity. Decompose the brief into its true modules and tasks." + _guardrail
      ),
      "gate_structure" => _operator,
      "hours" => Character.new(
        name: "Tank", tagline: "Cargo los programas que hagan falta, en paralelo", avatar: "🎛",
        classes: "bg-sky-500/20 text-sky-300 border-sky-400/40"
      ),
      "recover" => Character.new(
        name: "Trinity", tagline: "Rescato lo que se dio por perdido", avatar: "🏍",
        classes: "bg-fuchsia-500/20 text-fuchsia-300 border-fuchsia-400/40",
        persona: "You are Trinity: decisive and resourceful, you rescue what the first pass " \
                 "missed. Recover the doubtful task-hour estimates with care." + _guardrail
      ),
      "analysis" => Character.new(
        name: "El Oráculo", tagline: "Te diré la verdad, aunque no guste", avatar: "🔮",
        classes: "bg-violet-500/20 text-violet-300 border-violet-400/40"
      ),
      "gate_analysis" => _operator,
      "proposal" => Character.new(
        name: "El Arquitecto", tagline: "Compongo el constructo final", avatar: "📐",
        classes: "bg-cyan-500/20 text-cyan-300 border-cyan-400/40"
      )
    }.freeze

    def self.character_for(key) = CHARACTERS[key]
  end
end
