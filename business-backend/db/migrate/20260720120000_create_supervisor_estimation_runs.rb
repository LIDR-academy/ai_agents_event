# Session 14 — the supervisor-driven estimation flow + the human review inbox.
#
# The service IA runs a hand-built supervisor that routes at RUNTIME to four
# least-privilege agents, and PAUSES for a person when the estimate is not trustworthy
# enough (low confidence, outside the historical range, or no precedent at all). This
# row is the business backend's durable handle on one such run — and, when it is
# paused, an item in the review inbox.
#
# Unlike the Session 13 wizard, most runs never pause: the gate is conditional. That is
# why the inbox indexes on (run_state, status) — the interesting query is "what is
# waiting for a human right now", not "list everything".
class CreateSupervisorEstimationRuns < ActiveRecord::Migration[8.0]
  def change
    create_table :supervisor_estimation_runs do |t|
      t.text   :transcript, null: false
      # The checkpointer thread_id in the service IA. The service namespaces it as
      # "s14:<estimation_id>" internally (two graphs share one checkpoints table);
      # what we store and send is the bare id.
      t.string :estimation_id, null: false
      # "paused" while the review gate is open; "completed" when the run reached END.
      t.string :run_state, null: false, default: "paused"
      # "awaiting_human_review" while paused; then "validated" | "needs_review" |
      # "rejected".
      t.string :status
      # Why the run stopped: reasons, confidence, threshold, the estimate under review.
      t.jsonb :pending_review, null: false, default: {}

      # The agents' contributions.
      t.jsonb :requirements,   null: false, default: []
      t.jsonb :components,     null: false, default: []
      t.jsonb :budget_matches, null: false, default: []
      t.jsonb :estimate,       null: false, default: {}
      t.jsonb :validation,     null: false, default: {}
      t.float :confidence

      # The observability the session is about: what the supervisor decided and why,
      # and every action each agent took (including any denied by privilege).
      t.jsonb :routing_history,     null: false, default: []
      t.jsonb :agent_contributions, null: false, default: []
      t.jsonb :privilege_violations, null: false, default: []

      t.jsonb :human_decision, null: false, default: {}
      t.jsonb :errors_list,    null: false, default: []

      t.timestamps
    end

    add_index :supervisor_estimation_runs, :estimation_id, unique: true
    add_index :supervisor_estimation_runs, :created_at
    # The inbox query: everything currently waiting for a person.
    add_index :supervisor_estimation_runs, [ :run_state, :status ]
  end
end
