class AddRuntimeSnapshotToChatSessions < ActiveRecord::Migration[8.0]
  def change
    # Stores the full GET /sessions/:id payload from FastAPI:
    # last_resolved_tier, last_tier_rule, anchors_count, summary_chars,
    # plus message_count. Read by the side panel and tests.
    add_column :chat_sessions, :runtime_snapshot, :jsonb, null: false, default: {}
  end
end
