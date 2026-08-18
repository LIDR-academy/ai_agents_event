class CreateChatSessions < ActiveRecord::Migration[8.0]
  def change
    create_table :chat_sessions do |t|
      # Mirror of the UUID returned by the FastAPI POST /sessions endpoint.
      t.string :remote_session_id, null: false
      t.jsonb  :latest_metadata,   null: false, default: {}
      t.integer :turn_count,       null: false, default: 0
      t.timestamps
    end
    add_index :chat_sessions, :remote_session_id, unique: true

    # Optional FK so a transactional estimation can still live alone.
    add_reference :estimations, :chat_session, foreign_key: true, null: true
  end
end
