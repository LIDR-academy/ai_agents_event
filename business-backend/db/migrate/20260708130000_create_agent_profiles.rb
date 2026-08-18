# Session 12 (live console) — persists named, personalizable agent profiles. Each
# row is a preset for an agent (currently only the hand-written S12 agent): a
# name, a free-text persona injected into the system prompt, and a JSONB bag of
# the per-run knobs (model, reasoning_effort, max_iterations, search_top_k,
# search_distance_threshold). Applied by passing its config as per-call overrides
# to POST /v1/estimate/agent/run — the AI service stays the owner of behaviour.
class CreateAgentProfiles < ActiveRecord::Migration[8.0]
  def change
    create_table :agent_profiles do |t|
      t.string  :name, null: false
      t.string  :agent_type, null: false, default: "handwritten"
      t.text    :persona
      t.jsonb   :config, null: false, default: {}
      t.boolean :is_default, null: false, default: false
      t.timestamps
    end
    add_index :agent_profiles, [ :agent_type, :name ], unique: true
    add_index :agent_profiles, [ :agent_type, :is_default ]
  end
end
