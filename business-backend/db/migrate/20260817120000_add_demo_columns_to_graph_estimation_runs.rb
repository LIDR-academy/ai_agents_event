# Demo (evento) — the /demo namespace reuses this table rather than forking it, so the
# recorded artifacts are byte-identical to a real run and ``apply_run_state!`` stays the
# single write path. These five columns are all nullable/defaulted: existing master-course
# rows keep ``demo_mode: nil`` and are unaffected.
class AddDemoColumnsToGraphEstimationRuns < ActiveRecord::Migration[8.0]
  def change
    change_table :graph_estimation_runs, bulk: true do |t|
      # nil = master course · "live" = real run · "replay" = recorded playback
      t.string   :demo_mode
      # Which recorded leg is playing (0 = start, 1 = after gate 1, 2 = after gate 2).
      t.integer  :replay_leg, default: 0, null: false
      # Clock origin for the current leg: drives replay playback AND the live elapsed timer.
      t.datetime :leg_started_at
      # N tasks approved at gate 1 — the denominator of the fan-out counter.
      t.integer  :fanout_total
      # Playback multiplier; 4.0 turns a 62 s structure leg into ~16 s.
      t.float    :replay_speed, default: 4.0, null: false
    end

    add_index :graph_estimation_runs, :demo_mode
  end
end
