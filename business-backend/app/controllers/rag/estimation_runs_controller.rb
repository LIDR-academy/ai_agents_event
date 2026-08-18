# RAG estimation wizard (Session 9). Walks a transcript through the four
# pipeline stages — reformulation → retrieval → augmentation → generation —
# one screen at a time, then a human-verification step where the instructor
# edits the cost breakdown. Each stage is a member action that calls the
# matching FastAPI per-stage endpoint and persists its output on the run, so
# any stage can be re-run in isolation (e.g. retrieval with new filters).
#
# Synchronous, like the Chunking Lab: the heavy call is generation (gpt-5), so
# that one gets a longer timeout and the form_loading spinner.
module Rag
  class EstimationRunsController < ApplicationController
    GENERATE_TIMEOUT_SECONDS = 300

    def index
      @runs = Rag::EstimationRun.order(created_at: :desc).limit(20)
    end

    def new
      @run = Rag::EstimationRun.new
    end

    # Create the run and immediately run stage 1 (reformulation) so the wizard
    # opens on a populated screen.
    def create
      transcript = params.dig(:estimation_run, :transcript).to_s.strip
      @run = Rag::EstimationRun.new(transcript: transcript, idempotency_key: SecureRandom.uuid)
      unless @run.valid?
        flash.now[:alert] = "Pega una transcripción para empezar."
        return render :new, status: :unprocessable_entity
      end
      @run.save!
      guard_rag_errors do
        run_reformulation!
        redirect_to rag_estimation_run_path(@run, step: "reformulation")
      end
    end

    def show
      @run = Rag::EstimationRun.find(params[:id])
      @step = STEP_OR_CURRENT.call(@run, params[:step])
    end

    # --- stage member actions (each re-runnable) -----------------------------

    def reformulate
      @run = Rag::EstimationRun.find(params[:id])
      guard_rag_errors do
        run_reformulation!
        redirect_to rag_estimation_run_path(@run, step: "reformulation"),
                    notice: "Reformulación actualizada."
      end
    end

    # Phase 1 (Session 12) — the AGENT proposes the module→task structure as a free
    # decomposition of the brief (no retrieval, no sources, no hours). Same shape as
    # the deterministic /stages/structure plus the agent's one-step trace; the human
    # reviews the tree next. The hours are derived later by per-task search.
    def generate
      @run = Rag::EstimationRun.find(params[:id])
      guard_rag_errors do
        query = @run.reformulation_view&.query
        # Fixed agent per step (no selection): Neo drafts the structure.
        payload = rag_client(timeout: GENERATE_TIMEOUT_SECONDS).agent_generate_structure(
          query: query ? query.to_payload : {},
          config: {},
          persona: Agents::GraphFlow.character_for("structure")&.persona
        )
        @run.update!(
          generation: payload,
          structure: seed_structure(payload),
          status: "generated",
          current_step: "review"
        )
        # Clear downstream of review (stale hours / breakdown from a prior run),
        # keeping the structure we just seeded.
        @run.clear_downstream!("review")
        redirect_to rag_estimation_run_path(@run, step: "review")
      end
    end

    # Human review #1 done → persist the edited structure and derive hours per task.
    # Phase 2 (Session 12): the deterministic per-task vector search runs first, and
    # the AGENT re-searches only the tasks it could not ground (its recovery trace
    # rides along in ``task_hours``). Same shape as the deterministic /tasks/hours.
    def estimate_hours
      @run = Rag::EstimationRun.find(params[:id])
      modules = normalized_structure
      @run.update!(structure: { "modules" => modules })
      guard_rag_errors do
        # Fixed agent per step (no selection): Trinity recovers the doubtful hours.
        result = rag_client(timeout: GENERATE_TIMEOUT_SECONDS).agent_estimate_task_hours(
          modules: structure_for_api(modules),
          config: {},
          persona: Agents::GraphFlow.character_for("recover")&.persona
        )
        @run.update!(
          task_hours: result,
          adjusted_breakdown: seed_breakdown_with_hours(modules, result),
          status: "hours_estimated",
          current_step: "hours"
        )
        redirect_to rag_estimation_run_path(@run, step: "hours"),
                    notice: "Horas estimadas (determinista + recuperación del agente)."
      end
    end

    # Human review #2: persist the edited hours + rates as the confirmed estimate,
    # recomputing the total cost authoritatively.
    def verify
      @run = Rag::EstimationRun.find(params[:id])
      modules = normalized_cost_modules
      total_hours = modules.sum { |m| m["tasks"].sum { |t| t["estimated_hours"].to_i } }
      total_cost = modules.sum do |m|
        m["tasks"].sum { |t| t["estimated_hours"].to_i * t["rate_eur_per_hour"].to_i }
      end
      @run.update!(
        adjusted_breakdown: {
          "modules" => modules,
          "total_hours" => total_hours,
          "total_cost_eur" => total_cost,
          "confirmed_at" => Time.current.iso8601
        },
        status: "confirmed",
        current_step: "verification"
      )
      redirect_to rag_estimation_run_path(@run, step: "verification"),
                  notice: "Estimación confirmada y almacenada."
    end

    private

    # Default blended rate seeded into the breakdown; the human edits it per task.
    DEFAULT_RATE_EUR_PER_HOUR = 75

    STEP_OR_CURRENT = lambda do |run, requested|
      step = requested.presence || run.current_step
      Rag::EstimationRun::STEPS.include?(step) ? step : "transcript"
    end

    def rag_client(timeout: Rails.application.config.estimator_ai.timeout)
      EstimatorAi::RagEstimateClient.new(timeout: timeout)
    end

    def run_reformulation!
      payload = rag_client.reformulate(transcript: @run.transcript)
      @run.update!(reformulation: payload, status: "reformulated", current_step: "reformulation")
      @run.clear_downstream!("reformulation")
    end

    # Seed the editable STRUCTURE from the structure-only generation (no hours).
    # Empty when the estimate is insufficient — the user adds modules/tasks by hand.
    def seed_structure(generation_payload)
      estimate = generation_payload.fetch("estimate", {})
      modules = Array(estimate["modules"]).map do |m|
        m = m.transform_keys(&:to_s)
        {
          "name" => m["name"].to_s,
          "description" => m["description"].to_s,
          "tasks" => Array(m["tasks"]).map { |t| seed_structure_task(t) }
        }
      end
      { "modules" => modules }
    end

    def seed_structure_task(raw)
      raw = raw.transform_keys(&:to_s)
      { "name" => raw["name"].to_s, "description" => raw["description"].to_s }
    end

    # Merge the per-task hours estimates back into the structure to seed the
    # editable cost breakdown: each task gains estimated_hours / hours_reliability
    # / has_match (matched by module + task name) and a default rate.
    def seed_breakdown_with_hours(modules, hours_result)
      lookup = {}
      Array(hours_result["tasks"]).each do |t|
        t = t.transform_keys(&:to_s)
        lookup[[ t["module"].to_s, t["task"].to_s ]] = t
      end

      seeded = modules.map do |m|
        tasks = Array(m["tasks"]).map do |task|
          hit = lookup[[ m["name"].to_s, task["name"].to_s ]] || {}
          matched = hit.fetch("has_match", false)
          {
            "name" => task["name"], "description" => task["description"],
            "estimated_hours" => hit["estimated_hours"],
            "hours_reliability" => hit["reliability"],
            "has_match" => matched,
            # Session 11: carry the contradictory-sources range so the hours
            # screen can flag it (nil when the analogs agreed).
            "hours_range" => hit["hours_range"],
            "rate_eur_per_hour" => DEFAULT_RATE_EUR_PER_HOUR
          }
        end
        { "name" => m["name"], "description" => m["description"], "tasks" => tasks }
      end

      total_hours = seeded.sum { |m| m["tasks"].sum { |t| t["estimated_hours"].to_i } }
      {
        "modules" => seeded,
        "total_hours" => total_hours,
        "total_cost_eur" => total_hours * DEFAULT_RATE_EUR_PER_HOUR,
        "confirmed_at" => nil # nil = seeded draft, not yet human-confirmed
      }
    end

    # The shape POSTed to /v1/estimate/tasks/hours: modules → tasks (name + desc).
    def structure_for_api(modules)
      modules.map do |m|
        {
          name: m["name"],
          tasks: Array(m["tasks"]).map { |t| { name: t["name"], description: t["description"] } }
        }
      end
    end

    # --- param parsing (integer-indexed hashes from the Stimulus editor) --------

    # Review #1 structure: name / description, NO hours yet.
    def normalized_structure
      values_of(params[:modules]).filter_map do |raw_module|
        attrs = to_h(raw_module)
        name = attrs["name"].to_s.strip
        next if name.blank?

        tasks = values_of(attrs["tasks"]).filter_map do |t|
          ta = to_h(t)
          tname = ta["name"].to_s.strip
          next if tname.blank?

          { "name" => tname, "description" => ta["description"].to_s }
        end
        { "name" => name, "description" => attrs["description"].to_s, "tasks" => tasks }
      end
    end

    # Review #2 cost breakdown: hours + rate per task (and the carried metadata).
    def normalized_cost_modules
      values_of(params[:modules]).filter_map do |raw_module|
        attrs = to_h(raw_module)
        name = attrs["name"].to_s.strip
        next if name.blank?

        tasks = values_of(attrs["tasks"]).filter_map do |t|
          ta = to_h(t)
          tname = ta["name"].to_s.strip
          next if tname.blank?

          hours = ta["estimated_hours"].presence&.to_i
          { "name" => tname, "description" => ta["description"].to_s,
            "estimated_hours" => hours,
            "rate_eur_per_hour" => ta["rate_eur_per_hour"].to_i,
            "has_match" => !hours.nil? }
        end
        { "name" => name, "description" => attrs["description"].to_s, "tasks" => tasks }
      end
    end

    # Integer-indexed params arrive as a hash {"0" => {...}, "1" => {...}};
    # take the values in index order. Tolerates a plain array too.
    def values_of(collection)
      h = to_h(collection)
      h.is_a?(Hash) ? h.sort_by { |k, _| k.to_i }.map(&:last) : Array(collection)
    end

    def to_h(obj)
      obj.respond_to?(:to_unsafe_h) ? obj.to_unsafe_h : (obj || {})
    end

    # --- error handling (mirrors the Chunking Lab posture) -------------------
    # Inline rescue (not rescue_from): the EstimatorAi error taxonomy lives in
    # base_client.rb, so the constants only resolve once a client has loaded —
    # which always happens inside the yielded block before any error is raised.
    def guard_rag_errors
      yield
    rescue EstimatorAi::GuardrailViolation => e
      redirect_back_to_run("Entrada rechazada por guardarraíles: #{e.message}")
    rescue EstimatorAi::InvalidRequest => e
      redirect_back_to_run("Petición inválida: #{e.message}")
    rescue EstimatorAi::ServerError => e
      message = e.message.to_s
      hint =
        if message.include?("API key") || message.include?("401")
          "¿Coinciden RETRIEVAL_API_KEY / ESTIMATE_API_KEY con las del servicio IA?"
        elsif message.include?("not available")
          "El servicio de embeddings/LLM no está disponible en el estimator (¿falta la API key?)."
        else
          "Error del servicio IA: #{message}"
        end
      redirect_back_to_run(hint)
    rescue Faraday::TimeoutError, Faraday::ConnectionFailed => e
      redirect_back_to_run("El servicio IA no respondió a tiempo. La generación con gpt-5 puede " \
                           "tardar; reintenta. (#{e.class})")
    end

    def redirect_back_to_run(message)
      flash[:alert] = message
      if @run&.persisted?
        redirect_to rag_estimation_run_path(@run, step: @run.current_step)
      else
        redirect_to new_rag_estimation_run_path
      end
    end
  end
end
