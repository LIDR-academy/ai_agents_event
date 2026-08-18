class CreateEstimationRuns < ActiveRecord::Migration[8.0]
  def change
    create_table :estimation_runs do |t|
      t.text    :transcript,         null: false
      t.string  :status,             null: false, default: "started"      # wizard lifecycle
      t.string  :current_step,       null: false, default: "transcript"   # which screen to show
      t.jsonb   :reformulation,      null: false, default: {}             # { query:, search_text: }
      t.jsonb   :retrieval,          null: false, default: {}             # RetrievalResult + filters used
      t.jsonb   :augmentation,       null: false, default: {}             # AssembleResult
      t.jsonb   :generation,         null: false, default: {}             # GenerateResult (estimate + signals)
      t.jsonb   :adjusted_breakdown, null: false, default: {}             # human-verified version
      t.string  :idempotency_key
      t.integer :duration_ms                                              # wall-clock of the last stage call

      t.timestamps
    end
    add_index :estimation_runs, :created_at
  end
end
