# Session 13 — the graph-driven estimation wizard.
#
# The flow now lives as a LangGraph multi-agent graph inside the service IA that
# PAUSES at two human gates. This row is the business backend's durable handle on one
# such run: it holds the transcript, the ``estimation_id`` (== the graph's
# checkpointer thread_id), the current gate + its payload, and each artifact the graph
# produced (structure, estimate, analysis report, proposal). Resuming after a pause of
# minutes or days just re-reads this row and calls the service's resume endpoint.
class CreateGraphEstimationRuns < ActiveRecord::Migration[8.0]
  def change
    create_table :graph_estimation_runs do |t|
      t.text   :transcript, null: false
      # The checkpointer thread_id in the service IA (contract: thread_id == this).
      t.string :estimation_id, null: false
      # "paused" while a human gate is open; "completed" when the graph reached END.
      t.string :graph_state, null: false, default: "paused"
      # Which gate the run is paused at ("structure_review" | "final_review" | nil).
      t.string :current_gate
      t.string :status # "validated" | "needs_review" (set at gate 2)

      # The pending gate payload (what the human reviews) + each graph artifact.
      t.jsonb :pending_gate,    null: false, default: {}
      t.jsonb :structure,       null: false, default: {}
      t.jsonb :estimate,        null: false, default: {}
      t.jsonb :analysis_report, null: false, default: {}
      t.jsonb :task_hours,      null: false, default: {}
      t.text  :proposal

      t.timestamps
    end

    add_index :graph_estimation_runs, :estimation_id, unique: true
    add_index :graph_estimation_runs, :created_at
  end
end
