# The graph produces grounded HOURS but never money. These two knobs, set on the transcript
# screen before the run starts, are what turns those hours into a price at the end.
#
# Integers on purpose, matching the Session 9 wizard's ``rate_eur_per_hour``. The defaults
# mean the screen arrives filled in and a run started without touching them still prices.
class AddPricingToGraphEstimationRuns < ActiveRecord::Migration[8.0]
  def change
    change_table :graph_estimation_runs, bulk: true do |t|
      t.integer :rate_eur_per_hour, default: 75, null: false
      t.integer :contingency_pct,   default: 15, null: false
    end
  end
end
