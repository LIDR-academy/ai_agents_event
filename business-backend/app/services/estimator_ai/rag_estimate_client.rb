# Context client for the Session 9 RAG estimation pipeline. Talks to the
# per-stage wizard endpoints (POST /v1/estimate/stages/*) and the full
# transcript → estimate endpoint, all guarded by the estimate API key
# (X-API-Key header, injected once via BaseClient#default_headers).
#
# Session 15: BaseClient MERGES the shared X-Service-Token into these headers
# rather than replacing them, so these endpoints send both — the token to get
# through the app-wide middleware, the API key to satisfy the router.
#
# Stateless contract: each stage takes the prior stage's artifacts and returns
# the next one, so the caller (the wizard controller) persists everything and
# can re-run a single stage in isolation.
module EstimatorAi
  class RagEstimateClient < BaseClient
    def initialize(timeout: Rails.application.config.estimator_ai.timeout)
      super(
        timeout: timeout,
        default_headers: { "X-API-Key" => Rails.application.config.estimator_ai.estimate_api_key }
      )
    end

    # Stage 1 — transcript → { "query" => {...}, "search_text" => "..." }.
    def reformulate(transcript:)
      handle_response(json_conn.post("/v1/estimate/stages/reformulate", { transcript: transcript }))
    end

    # Structure-only generation (Session 10): a FREE decomposition of the brief —
    # no retrieval, no sources. Returns the same shape as the grounded generate
    # ({ estimate, fabricated_source_ids, coherent }) so the views parse it
    # unchanged; for a structure, citations are always clean.
    def generate_structure(query:)
      handle_response(json_conn.post("/v1/estimate/stages/structure", { query: query }))
    end

    # Grounded single-stage generation (Session 9, kept for the side-by-side
    # comparison demo; the Session 10 wizard uses #generate_structure instead).
    def generate(context_block:, query:, kept_chunks:, include_hours: true)
      handle_response(json_conn.post("/v1/estimate/stages/generate", {
        context_block: context_block, query: query, kept_chunks: kept_chunks,
        include_hours: include_hours
      }))
    end

    # Session 10 — per-task hours by vector search over the historical task corpus.
    # ``modules`` is [{ name:, tasks: [{ name:, description: }] }]; returns
    # { "tasks" => [TaskHoursEstimate, ...] } with reliability + neighbours.
    def estimate_task_hours(modules:)
      handle_response(json_conn.post("/v1/estimate/tasks/hours", { modules: modules }))
    end

    # Full pipeline (single shot, idempotent). Kept for the "compare against the
    # one-shot path" demo; the wizard itself drives the stages above.
    def from_transcript(transcript:, idempotency_key: nil)
      payload = { transcript: transcript, idempotency_key: idempotency_key }.compact
      handle_response(json_conn.post("/v1/estimate/from-transcript", payload))
    end

    # Session 12 — the hand-written agent drives the SAME two wizard phases as the
    # deterministic path above, around the human review gate. Both return the same
    # shapes as their deterministic siblings (so the views parse them unchanged),
    # plus an ``agent_trace``. ``config`` carries an agent profile's per-run
    # overrides; ``persona`` is appended to the agent system prompt. Blank values
    # are dropped so the service defaults win.

    # Phase 1 — the agent proposes the module→task structure (same shape as
    # #generate_structure, plus agent_trace).
    def agent_generate_structure(query:, config: {}, persona: nil)
      body = { query: query, persona: persona }.merge(config || {}).compact
      handle_response(json_conn.post("/v1/estimate/agent/structure", body))
    end

    # Phase 2 — deterministic per-task hours, then agent recovery on the tasks it
    # could not ground (same shape as #estimate_task_hours, plus agent_trace).
    def agent_estimate_task_hours(modules:, config: {}, persona: nil)
      body = { modules: modules, persona: persona }.merge(config || {}).compact
      handle_response(json_conn.post("/v1/estimate/agent/hours", body))
    end

    # Session 13 — the estimation flow is now a LangGraph multi-agent graph inside the
    # service IA that PAUSES at two human gates. The business backend orchestrates the
    # human part: START the run, then RESUME it with the person's decision at each gate.
    # All three return the same GraphRunState shape
    # ({ estimation_id, state: "paused"|"completed", pending_gate, structure, estimate,
    #    analysis_report, proposal, status, ... }); the pattern is stack-agnostic (any
    # HTTP client can drive resume). thread_id == estimation_id.

    # START — runs classifier + structure agents, pauses at human gate 1 (structure
    # review). Returns state "paused" with pending_gate.gate == "structure_review".
    def graph_start(transcript:, estimation_id: nil)
      body = { transcript: transcript, estimation_id: estimation_id }.compact
      handle_response(json_conn.post("/v1/estimate/graph", body))
    end

    # RESUME — feed the human's decision for the current gate. From gate 1
    # ({ approved:, modules: }) it runs the hours + analysis agents and pauses at gate 2;
    # from gate 2 ({ validated:, estimate_overrides:, want_proposal: }) it finishes
    # (optionally drafting a proposal). 409 if nothing is pending for this id.
    def graph_resume(estimation_id:, decision:)
      handle_response(json_conn.post("/v1/estimate/graph/#{estimation_id}/resume",
                                     { decision: decision }))
    end

    # STATE — read the current snapshot (pending gate + artifacts). Lets the UI recover
    # a run paused for minutes or days.
    def graph_state(estimation_id:)
      handle_response(json_conn.get("/v1/estimate/graph/#{estimation_id}/state"))
    end

    # --- LIVE variant (Session 13 live) --------------------------------------
    # The *_stream verbs run the graph in the BACKGROUND on the service and return
    # 202 immediately (state "running"); the wizard then polls #graph_progress to
    # fill a live per-agent panel. Same thread_id/idempotency as the blocking verbs.

    # START in the background. 202 + { state: "running", activity: [] }.
    def graph_start_stream(transcript:, estimation_id: nil)
      body = { transcript: transcript, estimation_id: estimation_id }.compact
      handle_response(json_conn.post("/v1/estimate/graph/stream", body))
    end

    # RESUME in the background. 202 + { state: "running" }. 409 if nothing pending.
    def graph_resume_stream(estimation_id:, decision:)
      handle_response(json_conn.post("/v1/estimate/graph/#{estimation_id}/resume-stream",
                                     { decision: decision }))
    end

    # POLL — { state: "running"|"paused"|"completed", activity: [...], + artifacts }.
    def graph_progress(estimation_id:)
      handle_response(json_conn.get("/v1/estimate/graph/#{estimation_id}/progress"))
    end

    # PROPOSAL — draft (or re-draft) the commercial proposal from the run's validated
    # estimate, WITHOUT re-running the graph. Returns the full CommercialProposal
    # { title, executive_summary, scope, total_engineer_days, body_markdown }.
    # 409 if the run has no validated estimate yet.
    def graph_proposal(estimation_id:, pricing: nil)
      handle_response(
        json_conn.post("/v1/estimate/graph/#{estimation_id}/proposal", { pricing: pricing }.compact)
      )
    end

    # --- Session 14: the SUPERVISOR flow -------------------------------------
    # A second, independent multi-agent flow in the service IA: a hand-built supervisor
    # routes at RUNTIME to four least-privilege agents. It coexists with the graph verbs
    # above (different endpoints, its own thread namespace on the same checkpointer).
    #
    # The contract is the one it has always been — transcript in, estimate + status out.
    # The one new thing the client must understand is that a run can PAUSE: when the
    # estimate is not trustworthy enough (low confidence, out of historical range, or no
    # precedent in the budgets) the response comes back status "awaiting_human_review"
    # with a `pending_review` describing why, and #supervisor_resume answers it.
    #
    # Blocking (no *_stream variant): this flow is short, and the didactic value is in
    # the pause, not in a live feed.

    # START — runs until the estimate is validated, or until the review gate trips.
    def supervisor_start(transcript:, estimation_id: nil)
      body = { transcript: transcript, estimation_id: estimation_id }.compact
      handle_response(json_conn.post("/v1/estimate/supervisor", body))
    end

    # RESUME — feed the reviewer's decision: "approve" | "adjust" | "reject".
    # `estimate_overrides` only matters for "adjust". 409 if nothing is pending.
    def supervisor_resume(estimation_id:, decision:, estimate_overrides: nil, note: nil)
      body = { decision: decision, estimate_overrides: estimate_overrides, note: note }.compact
      handle_response(json_conn.post("/v1/estimate/supervisor/#{estimation_id}/resume", body))
    end

    # STATE — read the current snapshot (pending review + artifacts + routing trace).
    def supervisor_state(estimation_id:)
      handle_response(json_conn.get("/v1/estimate/supervisor/#{estimation_id}/state"))
    end
  end
end
