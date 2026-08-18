# This file is auto-generated from the current state of the database. Instead
# of editing this file, please use the migrations feature of Active Record to
# incrementally modify your database, and then regenerate this schema definition.
#
# This file is the source Rails uses to define your schema when running `bin/rails
# db:schema:load`. When creating a new database, `bin/rails db:schema:load` tends to
# be faster and is potentially less error prone than running all of your
# migrations from scratch. Old migrations may fail to apply correctly if those
# migrations use external dependencies or application code.
#
# It's strongly recommended that you check this file into your version control system.

ActiveRecord::Schema[8.0].define(version: 2026_08_18_100000) do
  # These are extensions that must be enabled in order to support this database
  enable_extension "pg_catalog.plpgsql"

  create_table "active_storage_attachments", force: :cascade do |t|
    t.string "name", null: false
    t.string "record_type", null: false
    t.bigint "record_id", null: false
    t.bigint "blob_id", null: false
    t.datetime "created_at", null: false
    t.index ["blob_id"], name: "index_active_storage_attachments_on_blob_id"
    t.index ["record_type", "record_id", "name", "blob_id"], name: "index_active_storage_attachments_uniqueness", unique: true
  end

  create_table "active_storage_blobs", force: :cascade do |t|
    t.string "key", null: false
    t.string "filename", null: false
    t.string "content_type"
    t.text "metadata"
    t.string "service_name", null: false
    t.bigint "byte_size", null: false
    t.string "checksum"
    t.datetime "created_at", null: false
    t.index ["key"], name: "index_active_storage_blobs_on_key", unique: true
  end

  create_table "active_storage_variant_records", force: :cascade do |t|
    t.bigint "blob_id", null: false
    t.string "variation_digest", null: false
    t.index ["blob_id", "variation_digest"], name: "index_active_storage_variant_records_uniqueness", unique: true
  end

  create_table "agent_profiles", force: :cascade do |t|
    t.string "name", null: false
    t.string "agent_type", default: "handwritten", null: false
    t.text "persona"
    t.jsonb "config", default: {}, null: false
    t.boolean "is_default", default: false, null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["agent_type", "is_default"], name: "index_agent_profiles_on_agent_type_and_is_default"
    t.index ["agent_type", "name"], name: "index_agent_profiles_on_agent_type_and_name", unique: true
  end

  create_table "chat_sessions", force: :cascade do |t|
    t.string "remote_session_id", null: false
    t.jsonb "latest_metadata", default: {}, null: false
    t.integer "turn_count", default: 0, null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.jsonb "runtime_snapshot", default: {}, null: false
    t.index ["remote_session_id"], name: "index_chat_sessions_on_remote_session_id", unique: true
  end

  create_table "chunking_comparisons", force: :cascade do |t|
    t.jsonb "strategies", default: [], null: false
    t.jsonb "queries", default: [], null: false
    t.integer "top_k", default: 3, null: false
    t.string "corpus_label", default: "budgets_sample", null: false
    t.integer "corpus_count", default: 0, null: false
    t.jsonb "response_payload", default: {}, null: false
    t.integer "duration_ms"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["created_at"], name: "index_chunking_comparisons_on_created_at"
  end

  create_table "estimation_runs", force: :cascade do |t|
    t.text "transcript", null: false
    t.string "status", default: "started", null: false
    t.string "current_step", default: "transcript", null: false
    t.jsonb "reformulation", default: {}, null: false
    t.jsonb "retrieval", default: {}, null: false
    t.jsonb "augmentation", default: {}, null: false
    t.jsonb "generation", default: {}, null: false
    t.jsonb "adjusted_breakdown", default: {}, null: false
    t.string "idempotency_key"
    t.integer "duration_ms"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.jsonb "structure", default: {}, null: false
    t.jsonb "task_hours", default: {}, null: false
    t.index ["created_at"], name: "index_estimation_runs_on_created_at"
  end

  create_table "estimations", force: :cascade do |t|
    t.text "description", null: false
    t.string "project_type", null: false
    t.string "detail_level", null: false
    t.string "output_format", null: false
    t.jsonb "response_payload", default: {}, null: false
    t.string "prompt_version"
    t.boolean "cached", default: false, null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.bigint "chat_session_id"
    t.index ["chat_session_id"], name: "index_estimations_on_chat_session_id"
    t.index ["created_at"], name: "index_estimations_on_created_at"
  end

  create_table "graph_estimation_runs", force: :cascade do |t|
    t.text "transcript", null: false
    t.string "estimation_id", null: false
    t.string "graph_state", default: "paused", null: false
    t.string "current_gate"
    t.string "status"
    t.jsonb "pending_gate", default: {}, null: false
    t.jsonb "structure", default: {}, null: false
    t.jsonb "estimate", default: {}, null: false
    t.jsonb "analysis_report", default: {}, null: false
    t.jsonb "task_hours", default: {}, null: false
    t.text "proposal"
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.string "proposal_title"
    t.integer "rate_eur_per_hour", default: 75, null: false
    t.integer "contingency_pct", default: 15, null: false
    t.index ["created_at"], name: "index_graph_estimation_runs_on_created_at"
    t.index ["estimation_id"], name: "index_graph_estimation_runs_on_estimation_id", unique: true
  end

  create_table "index_runs", force: :cascade do |t|
    t.string "job_id", null: false
    t.string "chunk_type", default: "budget_component", null: false
    t.integer "submitted_count", default: 0, null: false
    t.string "status", default: "pending", null: false
    t.integer "documents_processed", default: 0, null: false
    t.jsonb "before_stats", default: {}, null: false
    t.jsonb "after_stats", default: {}, null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["job_id"], name: "index_index_runs_on_job_id"
  end

  create_table "supervisor_estimation_runs", force: :cascade do |t|
    t.text "transcript", null: false
    t.string "estimation_id", null: false
    t.string "run_state", default: "paused", null: false
    t.string "status"
    t.jsonb "pending_review", default: {}, null: false
    t.jsonb "requirements", default: [], null: false
    t.jsonb "components", default: [], null: false
    t.jsonb "budget_matches", default: [], null: false
    t.jsonb "estimate", default: {}, null: false
    t.jsonb "validation", default: {}, null: false
    t.float "confidence"
    t.jsonb "routing_history", default: [], null: false
    t.jsonb "agent_contributions", default: [], null: false
    t.jsonb "privilege_violations", default: [], null: false
    t.jsonb "human_decision", default: {}, null: false
    t.jsonb "errors_list", default: [], null: false
    t.datetime "created_at", null: false
    t.datetime "updated_at", null: false
    t.index ["created_at"], name: "index_supervisor_estimation_runs_on_created_at"
    t.index ["estimation_id"], name: "index_supervisor_estimation_runs_on_estimation_id", unique: true
    t.index ["run_state", "status"], name: "index_supervisor_estimation_runs_on_run_state_and_status"
  end

  add_foreign_key "active_storage_attachments", "active_storage_blobs", column: "blob_id"
  add_foreign_key "active_storage_variant_records", "active_storage_blobs", column: "blob_id"
  add_foreign_key "estimations", "chat_sessions"
end
