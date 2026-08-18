Rails.application.routes.draw do
  # Define your application routes per the DSL in https://guides.rubyonrails.org/routing.html

  # Reveal health status on /up that returns 200 if the app boots with no exceptions, otherwise 500.
  # Can be used by load balancers and uptime monitors to verify that the app is live.
  get "up" => "rails/health#show", as: :rails_health_check

  # Render dynamic PWA files from app/views/pwa/* (remember to link manifest in application.html.erb)
  # get "manifest" => "rails/pwa#manifest", as: :pwa_manifest
  # get "service-worker" => "rails/pwa#service_worker", as: :pwa_service_worker

  resources :estimations, only: [ :index, :new, :create, :show ]

  # Session 5 conversational flow. ``create`` is bound to a specific session
  # (POST /chat_sessions/:id) — :new creates the underlying session lazily
  # when the page first loads.
  resources :chat_sessions, only: [ :new, :show, :destroy ] do
    member do
      post :create
    end
  end

  # Session 7 RAG context: chunking strategy comparison lab.
  # Session 9 RAG context: the transcript → grounded-estimate wizard. One
  # canonical resource + a member action per pipeline stage (each re-runnable),
  # plus the human-verification PATCH.
  namespace :rag do
    resources :chunking_comparisons, only: [ :index, :new, :create, :show ]

    # Corpus / Índice (Session 11): add new information to the vector DB and poll
    # the async indexing job until the corpus grows.
    resources :index_runs, only: [ :index, :new, :create, :show ] do
      member { get :status }
    end

    resources :estimation_runs, only: [ :index, :new, :create, :show ] do
      member do
        post  :reformulate
        post  :generate       # S12 agent proposes the structure (free decomposition)
        post  :estimate_hours # save reviewed structure → deterministic hours + agent recovery
        patch :verify         # edit hours/rates, compute cost, confirm + store
      end
    end

    # Session 13 — the graph-driven wizard: the service IA orchestrates a LangGraph
    # multi-agent flow that pauses at two human gates; we START it and RESUME it.
    resources :graph_estimation_runs, only: [ :index, :new, :create, :show ] do
      member do
        post :resume_structure # human gate 1 → resume with the reviewed breakdown
        post :resume_final     # human gate 2 → resume with the final validation
        get  :progress         # live per-agent activity feed (polled while a leg runs)
        post :generate_proposal # draft/redraft the commercial proposal after completion
        get  :proposal_pdf      # download the proposal as a PDF (Prawn)
        get  :proposal_md       # the same document as Markdown, budget tables included
      end
    end

    # Session 14 — the supervisor flow + the human review inbox. The service IA routes
    # at runtime to four least-privilege agents and pauses ONLY when the estimate is
    # not trustworthy enough, so #index is a work queue rather than a wizard listing.
    resources :supervisor_estimation_runs, only: [ :index, :new, :create, :show ] do
      member do
        post :resume # the reviewer's decision: approve / adjust / reject
      end
    end
  end

  # Session 12 — agents console: named, personalizable profiles for the
  # hand-written agent (the ACB is shown read-only here).
  namespace :agents do
    resources :profiles
    # Session 13 — read-only visual resource of the multi-agent graph flow.
    get "graph_flow", to: "graph_flow#show"
  end

  # Atajo para el evento: una URL corta y memorizable que lleva al flujo de estimación
  # por grafo de agentes. Sin controlador ni vista propios — es el flujo del proyecto.
  # 302 y no el 301 que Rails pone por defecto: un permanente se queda cacheado en el
  # navegador y luego no hay forma de reapuntar /demo a otro sitio.
  get "demo", to: redirect("/rag/graph_estimation_runs/new", status: 302)

  # Runtime model configuration of the AI service (Ajustes).
  resource :ai_settings, only: [ :show, :update ]

  # Landing dashboard: one card per context of the Master's journey.
  root "home#index"
end
