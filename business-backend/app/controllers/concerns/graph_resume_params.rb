# Parsing of the integer-indexed module→task params the shared editor partials submit
# (app/views/rag/estimation_runs/_editable_module.html.erb and friends, driven by the
# ``estimate-modules-editor`` Stimulus controller).
#
# Extracted from Rag::GraphEstimationRunsController so the /demo namespace drives the
# very same human gates without copying the parsing. Both include it; the behaviour is
# byte-identical, which is the point — the demo must not be a reimplementation.
#
# Expects the including controller to expose ``@run`` (a Rag::GraphEstimationRun or a
# subclass) for #estimate_modules_with_edited_hours.
module GraphResumeParams
  extend ActiveSupport::Concern

  private

  # Gate 1 → the ``[{ name:, tasks: [{ name:, description: }] }]`` shape the resume
  # decision wants. Blank names are dropped, so deleting a row in the editor deletes it
  # from the run.
  def reviewed_modules
    values_of(params[:modules]).filter_map do |raw_module|
      attrs = to_h(raw_module)
      name = canonical_text(attrs["name"])
      next if name.blank?

      tasks = values_of(attrs["tasks"]).filter_map do |t|
        ta = to_h(t)
        tname = canonical_text(ta["name"])
        next if tname.blank?

        { "name" => tname, "description" => canonical_text(ta["description"]) }
      end
      { "name" => name, "tasks" => tasks }
    end
  end

  # Undo HTML entity encoding on submitted names before they leave for the service.
  #
  # Observed in production runs 8 and 17: modules whose name contains "&" came back
  # from the gate-1 form as "Architecture &amp;amp; Project Setup". The damage is not
  # cosmetic. The service keys the per-task hours rows by (module, task), and the
  # recovery agent returns those names normalised — so the escaped form makes the merge
  # INSERT instead of UPDATE, the recovered hours never reach build_estimate, and the
  # human ends up retyping them. It also travels into the embedding query text, so
  # every fan-out search for those modules is run against a polluted string.
  #
  # Normalising here fixes both consequences at the single boundary where browser text
  # enters, independently of which render path produced the field.
  def canonical_text(value)
    CGI.unescapeHTML(value.to_s).strip
  end

  # Gate 2 → patch the stored estimate's module→task tree with the hours the human
  # edited (matched BY INDEX — the structure is read-only at gate 2, so indices align
  # 1:1). Only ``estimated_hours`` changes; the service recomputes totals and the
  # grounded ratio from the new hours.
  def estimate_modules_with_edited_hours
    Array((@run.estimate || {})["modules"]).each_with_index.map do |mod, m|
      tasks = Array(mod["tasks"]).each_with_index.map do |task, t|
        raw = params.dig(:modules, m.to_s, :tasks, t.to_s, :estimated_hours).to_s.strip
        task.merge("estimated_hours" => (raw.blank? ? nil : raw.to_f))
      end
      mod.merge("tasks" => tasks)
    end
  end

  # Total number of tasks in an approved-module list — the denominator of the fan-out
  # counter (one Send per task).
  def task_count(modules)
    Array(modules).sum { |m| Array(m["tasks"]).size }
  end

  def values_of(collection)
    h = to_h(collection)
    h.is_a?(Hash) ? h.sort_by { |k, _| k.to_i }.map(&:last) : Array(collection)
  end

  def to_h(obj)
    obj.respond_to?(:to_unsafe_h) ? obj.to_unsafe_h : (obj || {})
  end
end
