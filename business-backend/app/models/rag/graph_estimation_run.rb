# Session 13 — AR root of the GRAPH-driven estimation wizard.
#
# Where the Session 12 wizard (Rag::EstimationRun) choreographed the flow itself —
# calling one FastAPI stage endpoint per screen — this wizard delegates the whole
# orchestration to a LangGraph multi-agent graph inside the service IA. The graph
# PAUSES at two human gates; this row is the durable handle the business backend
# keeps so it can render each gate and RESUME the run when the person approves.
#
# The service IA owns the state (its Postgres checkpointer, keyed by ``estimation_id``
# == the graph thread_id). We mirror just enough here to render the current screen and
# to survive a pause of minutes or days: each ``GraphRunState`` the service returns is
# persisted into the JSONB columns below.
module Rag
  class GraphEstimationRun < ApplicationRecord
    self.table_name = "graph_estimation_runs"

    # The two human gates the graph pauses at, in order.
    GATE_STRUCTURE = "structure_review".freeze
    GATE_FINAL     = "final_review".freeze

    validates :transcript, presence: true
    validates :estimation_id, presence: true, uniqueness: true
    # Pricing knobs, set on the transcript screen before the run starts.
    validates :rate_eur_per_hour, numericality: { in: 0..1000, only_integer: true }
    validates :contingency_pct,   numericality: { in: 0..100,  only_integer: true }

    # The graph is executing a leg in the background (between two gates); the show
    # page renders the live per-agent panel and polls #progress until it pauses/ends.
    def running? = graph_state == "running"
    def paused? = graph_state == "paused"
    def completed? = graph_state == "completed"
    def at_structure_gate? = current_gate == GATE_STRUCTURE
    def at_final_gate? = current_gate == GATE_FINAL

    # The modules→tasks the graph proposed (reviewed at gate 1). Reuses the same
    # WorkModuleView the Session 12 wizard renders, so the editor partials are shared.
    def structure_modules
      Array(structure["modules"]).map { |raw| Rag::WorkModuleView.from_hash(raw) }
    end

    def structure? = structure.present? && structure["modules"].present?

    # The estimate the hours agent built (modules→tasks with engineer_hours), shown at
    # gate 2 and after completion.
    # The run's rate is grafted onto every task on the way out, which is what lights up the
    # already-existing ``TaskItemView#cost_eur`` / ``WorkModuleView#subtotal_cost`` (dead
    # code until now) instead of writing a second cost calculation for the graph flow.
    def estimate_modules
      Array(estimate["modules"]).map { |raw| Rag::WorkModuleView.from_hash(with_rate(raw)) }
    end

    def estimate? = estimate.present? && estimate["modules"].present?

    def total_engineer_days = estimate["total_engineer_days"].to_i

    def total_engineer_hours = estimate["total_engineer_hours"].to_f

    def analysis_report? = analysis_report.present? && analysis_report["summary"].present?

    def proposal? = proposal.present?

    # Money for this run. See Rag::Pricing — computed here, never by a model.
    def pricing = Rag::Pricing.for(self)

    # The budget as data, for the markdown and the PDF alike. See Rag::BudgetBreakdown.
    def budget = Rag::BudgetBreakdown.for(self)

    # Headings that have no business in a client-facing proposal: how much historical
    # precedent the estimate had is an internal signal, and it stays on the result screen.
    RELIABILITY_HEADING = /\A(#+)\s*.*(fiabilidad|confianza|reliability|confidence|riesgo|risk)/i

    # The agent's prose with any reliability section removed, heading and body alike.
    #
    # The prompt forbids writing one, but the model is not deterministic and this gets
    # projected live: run 20 grew a "Reliability and Confidence" section the day after the
    # ban went into the input. So the guarantee is made here instead — everything that
    # renders the proposal goes through this one method.
    def proposal_prose
      lines = proposal.to_s.lines
      kept = []
      skipping_level = nil

      lines.each do |line|
        if (m = line.match(/\A(#+)\s/))
          level = m[1].length
          # A heading at the same or higher level ends whatever we were skipping.
          skipping_level = nil if skipping_level && level <= skipping_level
          skipping_level = level if skipping_level.nil? && line.match?(RELIABILITY_HEADING)
        end
        kept << line if skipping_level.nil?
      end
      kept.join.strip
    end

    # The full proposal DOCUMENT: the agent's prose plus the budget section composed here.
    # ``proposal`` in the database keeps holding the prose alone, so regenerating never
    # duplicates a table and nothing model-written ever carries a figure.
    def proposal_markdown
      return proposal_prose unless proposal? && budget.any?

      "#{proposal_prose.rstrip}\n\n#{budget.to_markdown}"
    end

    # Merge a GraphRunState (from the service IA) into this row. One mapping used by
    # both start and resume, so the persisted shape never drifts from the contract.
    def apply_run_state!(run_state)
      run_state = run_state.to_h.stringify_keys
      gate = run_state["pending_gate"] || {}
      payload = (gate["payload"] || {})
      assign_attributes(
        graph_state: run_state["state"] || "paused",
        current_gate: gate["gate"],
        status: run_state["status"],
        pending_gate: gate,
        # At gate 1 the structure lives in the gate payload; afterwards it stays put.
        structure: payload["structure"] || structure,
        estimate: run_state["estimate"] || estimate,
        analysis_report: run_state["analysis_report"] || analysis_report,
        task_hours: { "tasks" => run_state["task_hours"] || task_hours["tasks"] || [] },
        proposal: run_state["proposal"] || proposal
      )
      save!
    end

    private

    def with_rate(mod)
      return mod unless rate_eur_per_hour.to_i.positive?

      tasks = Array(mod["tasks"]).map { |task| task.merge("rate_eur_per_hour" => rate_eur_per_hour) }
      mod.merge("tasks" => tasks)
    end
  end
end
