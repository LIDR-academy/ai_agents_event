# The /demo namespace (a standalone event-demo surface) was removed: the demo is now given
# on the real wizard at /rag/graph_estimation_runs. These five columns only ever backed that
# surface, so they come out and the schema stops carrying ghost columns.
#
# The ROWS stay. They are genuine graph runs — they were produced by the real graph — and a
# completed one in the history is the fallback if the live run fails on stage.
class RemoveDemoColumnsFromGraphEstimationRuns < ActiveRecord::Migration[8.0]
  def change
    remove_index :graph_estimation_runs, :demo_mode
    remove_column :graph_estimation_runs, :demo_mode, :string
    remove_column :graph_estimation_runs, :replay_leg, :integer, default: 0, null: false
    remove_column :graph_estimation_runs, :leg_started_at, :datetime
    remove_column :graph_estimation_runs, :fanout_total, :integer
    remove_column :graph_estimation_runs, :replay_speed, :float, default: 4.0, null: false
  end
end
