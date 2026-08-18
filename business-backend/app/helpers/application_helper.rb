module ApplicationHelper
  # The navbar, as data. One entry per context; ``key`` drives both the active highlight
  # and the demo filter below.
  NAV = [
    { key: "estimations",           label: "Estimación",   path: :new_estimation_path },
    { key: "chat_sessions",         label: "Conversación", path: :new_chat_session_path },
    { key: "chunking_comparisons",  label: "RAG Lab",      path: :new_rag_chunking_comparison_path },
    { key: "estimation_runs",       label: "RAG Wizard",   path: :new_rag_estimation_run_path },
    { key: "graph_estimation_runs", label: "Grafo",        path: :new_rag_graph_estimation_run_path },
    { key: "agents",                label: "Agentes",      path: :agents_profiles_path },
    { key: "ai_settings",           label: "Ajustes",      path: :ai_settings_path }
  ].freeze

  # The two sections the event demo uses. On those screens the navbar shrinks to just
  # them, so nothing else in the app is one click away while projecting.
  #
  # It covers BOTH — not only the graph flow — on purpose: "Agentes" stays clickable, and
  # if the full navbar came back the moment you followed it, the trimming would be undone
  # by the only other link left.
  DEMO_NAV_KEYS = %w[graph_estimation_runs agents].freeze

  # Which nav entry the current screen belongs to. Everything under the ``agents``
  # namespace (profiles + the read-only flow diagram) answers to the same entry.
  def nav_key
    controller_path.start_with?("agents/") ? "agents" : controller_name
  end

  def nav_items
    DEMO_NAV_KEYS.include?(nav_key) ? NAV.select { |i| DEMO_NAV_KEYS.include?(i[:key]) } : NAV
  end

  # Euros with SPANISH separators: "48.450 €", not the "48,450 €" Rails produces under the
  # default :en locale — which a Spanish reader parses as forty-eight point four five.
  # The app has no i18n setup, so the separators go here rather than in a locale file.
  def format_eur(amount)
    return nil if amount.nil?

    number_to_currency(amount, unit: "€", format: "%n %u", precision: 0,
                               delimiter: ".", separator: ",")
  end

  # Hours render through one formatter so "152.0 h" never reaches a slide. See Rag::Hours.
  def format_hours(value) = Rag::Hours.format(value)

  def nav_link_classes(item)
    state = item[:key] == nav_key ? "text-brand" : "text-white/80"
    "font-bold tracking-tight hover:text-white #{state}"
  end
end
