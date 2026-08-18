class CreateChunkingComparisons < ActiveRecord::Migration[8.0]
  def change
    create_table :chunking_comparisons do |t|
      t.jsonb   :strategies,       null: false, default: []   # requested strategy names
      t.jsonb   :queries,          null: false, default: []   # requested query strings
      t.integer :top_k,            null: false, default: 3
      t.string  :corpus_label,     null: false, default: "budgets_sample"
      t.integer :corpus_count,     null: false, default: 0
      t.jsonb   :response_payload, null: false, default: {}   # full CompareResponse from FastAPI
      t.integer :duration_ms                                  # wall-clock of the FastAPI call

      t.timestamps
    end
    add_index :chunking_comparisons, :created_at
  end
end
