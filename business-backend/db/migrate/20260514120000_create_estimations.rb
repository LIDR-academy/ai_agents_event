class CreateEstimations < ActiveRecord::Migration[8.0]
  def change
    create_table :estimations do |t|
      t.text     :description,      null: false
      t.string   :project_type,     null: false
      t.string   :detail_level,     null: false
      t.string   :output_format,    null: false
      t.jsonb    :response_payload, null: false, default: {}
      t.string   :prompt_version
      t.boolean  :cached,           null: false, default: false

      t.timestamps
    end

    add_index :estimations, :created_at
  end
end
