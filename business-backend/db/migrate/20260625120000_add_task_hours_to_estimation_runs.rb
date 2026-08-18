# Session 10: the estimation flow splits structure from hours. Generation now
# produces a module→task STRUCTURE without hours (reviewed by a human), then the
# hours are derived per task by vector search and validated/edited before being
# confirmed. Two new JSONB columns hold the intermediate artifacts:
#   - structure: the human-reviewed module→task tree (no hours yet)
#   - task_hours: the per-task vector-search result (hours + reliability + neighbours)
# The existing adjusted_breakdown column carries the final confirmed estimate
# (hours + rate + cost per task).
class AddTaskHoursToEstimationRuns < ActiveRecord::Migration[8.0]
  def change
    add_column :estimation_runs, :structure, :jsonb, default: {}, null: false
    add_column :estimation_runs, :task_hours, :jsonb, default: {}, null: false
  end
end
