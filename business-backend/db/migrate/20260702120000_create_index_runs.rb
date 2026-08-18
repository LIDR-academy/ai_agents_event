# Session 11 — persists each corpus-expansion run (add new information to the
# vector DB). One row per triggered async job; the JSONB payload keeps the
# before/after corpus stats and the latest job snapshot so a completed run can be
# revisited. Mirrors the Rag::ChunkingComparison AR-root + JSONB pattern.
class CreateIndexRuns < ActiveRecord::Migration[8.0]
  def change
    create_table :index_runs do |t|
      t.string :job_id, null: false
      t.string :chunk_type, null: false, default: "budget_component"
      t.integer :submitted_count, null: false, default: 0
      t.string :status, null: false, default: "pending"
      t.integer :documents_processed, null: false, default: 0
      t.jsonb :before_stats, null: false, default: {}
      t.jsonb :after_stats, null: false, default: {}
      t.timestamps
    end
    add_index :index_runs, :job_id
  end
end
