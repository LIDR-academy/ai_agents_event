# ActiveRecord root of the RAG wizard. Persists one transcript → grounded-estimate
# run as it advances stage by stage: each stage writes its FastAPI response into a
# JSONB column, and ``current_step`` tracks which screen the wizard host renders.
#
# Session 10 splits structure from hours. After generation (now structure-only) a
# human reviews the module→task tree (``review``), the hours are derived per task
# by vector search (``hours``), and finally the human edits hours/rates and
# confirms (``verification``). Mirrors the Rag::ChunkingComparison pattern (AR root
# + JSONB + PORO views).
module Rag
  class EstimationRun < ApplicationRecord
    self.table_name = "estimation_runs"

    # Ordered wizard steps. ``transcript`` is the entry form; the rest map to a
    # pipeline stage / human gate and to a JSONB column of the same family.
    # Session 10: the structure is a FREE decomposition of the brief (no retrieval
    # before it); retrieval re-enters per task in the ``hours`` step.
    # Session 12: the hand-written agent now DRIVES those two phases (it proposes
    # the structure in ``generation`` and recovers the ungrounded tasks' hours in
    # ``hours``), around the same human ``review``/``verification`` gates — there is
    # no separate agent step. Its trace rides along in the ``generation`` /
    # ``task_hours`` JSONB.
    STEPS = %w[
      transcript reformulation generation review hours verification
    ].freeze

    # Stage → the JSONB column that must be populated for that step to render.
    STEP_COLUMNS = {
      "reformulation" => :reformulation,
      "generation"    => :generation,          # structure-only (no RAG, no hours)
      "review"        => :structure,           # human-reviewed structure
      "hours"         => :task_hours,          # per-task semantic search (hybrid+rerank)
      "verification"  => :adjusted_breakdown   # confirmed estimate (hours+rate+cost)
    }.freeze

    validates :transcript, presence: true

    # --- typed views over the per-stage JSONB --------------------------------

    def reformulation_view
      Rag::ReformulationView.from_hash(reformulation) if reformulation.present?
    end

    def generation_view
      Rag::GenerationView.from_hash(generation) if generation.present?
    end

    # The human-reviewed structure: modules→tasks WITHOUT hours yet.
    def structure_modules
      Array(structure["modules"]).map { |raw| Rag::WorkModuleView.from_hash(raw) }
    end

    def structure? = structure.present? && structure["modules"].present?

    # The per-task hours estimates (vector search): { "tasks" => [...] }.
    def task_hours_view
      Rag::TaskHoursView.from_hash(task_hours) if task_hours.present?
    end

    # The human-confirmed breakdown ({ "modules" => [...], "total_hours" => n,
    # "total_cost_eur" => n, "confirmed_at" => iso }). Falsy until saved.
    def adjusted_modules
      Array(adjusted_breakdown["modules"]).map { |raw| Rag::WorkModuleView.from_hash(raw) }
    end

    def adjusted_total_hours = adjusted_breakdown["total_hours"].to_i

    def adjusted_total_cost = adjusted_breakdown["total_cost_eur"].to_i

    def adjusted? = adjusted_breakdown.present? && adjusted_breakdown["modules"].present?

    def confirmed? = adjusted_breakdown["confirmed_at"].present?

    # Session 12: the hand-written agent's reason→act→observe trace, carried inside
    # the phase JSONB it drove. Phase 1 (structure) has a thin one-step trace;
    # phase 2 (hours) has the STEP N recovery trace (empty steps = nothing needed
    # recovery). Nil when the phase was produced by the deterministic path.
    def generation_agent_trace
      raw = generation["agent_trace"] if generation.present?
      Rag::AgentTraceView.from_hash(raw) if raw.present?
    end

    def hours_agent_trace
      raw = task_hours["agent_trace"] if task_hours.present?
      Rag::AgentTraceView.from_hash(raw) if raw.present?
    end

    # --- wizard state --------------------------------------------------------

    def step_complete?(step)
      column = STEP_COLUMNS[step.to_s]
      column ? self[column].present? : true
    end

    # When an earlier stage is re-run, downstream artifacts no longer match the
    # new input — null them so the wizard never shows a stale structure, hours or
    # estimate built from superseded chunks.
    def clear_downstream!(from_step)
      idx = STEPS.index(from_step.to_s)
      return if idx.nil?

      cleared = {}
      STEPS[(idx + 1)..].each do |step|
        column = STEP_COLUMNS[step]
        cleared[column] = {} if column && self.class.column_names.include?(column.to_s)
      end
      update!(cleared) if cleared.any?
    end
  end
end
