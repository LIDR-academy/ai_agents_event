module Rag
  # The budget, as data, rendered two ways: a markdown section for the proposal document and
  # plain row arrays for the PDF's prawn-tables.
  #
  # It lives here rather than in either renderer for one reason: the two documents must not
  # be able to disagree. Same object, two views. It is also why the AGENT never writes these
  # tables — 75 rows of figures typed by a model is exactly the arithmetic the proposal
  # prompt spends a paragraph forbidding.
  class BudgetBreakdown
    Row = Struct.new(:label, :count, :hours, :cost, keyword_init: true)

    def self.for(run) = new(run)

    def initialize(run)
      @run = run
      @pricing = run.pricing
      @modules = run.estimate_modules
    end

    attr_reader :pricing

    def priced? = pricing.priced?

    def any? = @modules.any?

    # One row per module, plus the TOTAL. The total comes from ``pricing`` and the run's own
    # hours rather than from summing the column above it: same rule as everywhere else, and
    # on real data the two agree to the cent.
    def module_rows
      @modules.map do |mod|
        Row.new(label: mod.name.to_s, count: mod.tasks.size,
                hours: mod.subtotal_hours, cost: mod.subtotal_cost)
      end
    end

    def total_row
      Row.new(label: "TOTAL", count: @modules.sum { |m| m.tasks.size },
              hours: @run.total_engineer_hours, cost: pricing.base)
    end

    # [[module name, [task rows]], …] — the per-task detail, grouped so each module keeps
    # its own little table instead of one 75-row wall.
    def task_groups
      @modules.map do |mod|
        rows = mod.tasks.map do |task|
          Row.new(label: task.name.to_s, count: nil,
                  hours: task.estimated_hours.to_f, cost: task.cost_eur)
        end
        [ mod.name.to_s, rows ]
      end
    end

    # The whole "## Presupuesto" section. Columns are padded so the table reads correctly
    # both as markdown source and dumped into a monospaced block.
    def to_markdown
      return "" unless any?

      parts = [ "## Presupuesto", "", summary_table ]
      parts += [ "", price_lines ] if priced?
      parts += [ "", "### Detalle por tarea", "", detail_tables ]
      parts.join("\n").rstrip + "\n"
    end

    private

    def summary_table
      headers = priced? ? [ "Módulo", "Tareas", "Horas", "Coste" ] : [ "Módulo", "Tareas", "Horas" ]
      body = module_rows.map { |r| summary_cells(r) }
      markdown_table(headers, body + [ summary_cells(total_row, bold: true) ],
                     align: [ :left ] + [ :right ] * (headers.size - 1))
    end

    def summary_cells(row, bold: false)
      wrap = ->(text) { bold ? "**#{text}**" : text }
      cells = [ wrap.call(row.label), wrap.call(row.count.to_s),
                wrap.call("#{Rag::Hours.format(row.hours)} h") ]
      cells << wrap.call(eur(row.cost)) if priced?
      cells
    end

    def detail_tables
      headers = priced? ? [ "Tarea", "Horas", "Coste" ] : [ "Tarea", "Horas" ]
      task_groups.map do |name, rows|
        cells = rows.map do |r|
          row = [ r.label, "#{Rag::Hours.format(r.hours)} h" ]
          row << eur(r.cost) if priced?
          row
        end
        [ "#### #{name}", "",
          markdown_table(headers, cells, align: [ :left ] + [ :right ] * (headers.size - 1)),
          "" ].join("\n")
      end.join("\n")
    end

    def price_lines
      lines = [ "- **Base**: #{Rag::Hours.format(pricing.hours)} h × " \
                "#{pricing.rate_eur_per_hour} €/h = #{eur(pricing.base)}" ]
      if pricing.contingency?
        lines << "- **Contingencia** (#{pricing.contingency_pct}%): #{eur(pricing.contingency)}"
      end
      lines << "- **Total**: #{eur(pricing.total)}"
      lines.join("\n")
    end

    # A padded markdown table. The padding is what makes it legible when the markdown is
    # shown raw, which is how the proposal is displayed today.
    def markdown_table(headers, rows, align:)
      widths = headers.each_with_index.map do |h, i|
        ([ h ] + rows.map { |r| r[i].to_s }).map(&:length).max
      end
      sep = widths.each_with_index.map { |w, i| align[i] == :right ? "#{'-' * (w - 1)}:" : "-" * w }
      ([ line(headers, widths, align), line(sep, widths, [ :left ] * widths.size) ] +
        rows.map { |r| line(r, widths, align) }).join("\n")
    end

    def line(cells, widths, align)
      padded = cells.each_with_index.map do |cell, i|
        align[i] == :right ? cell.to_s.rjust(widths[i]) : cell.to_s.ljust(widths[i])
      end
      "| #{padded.join(' | ')} |"
    end

    def eur(amount)
      "#{amount.round.to_s.reverse.scan(/\d{1,3}/).join('.').reverse} €"
    end
  end
end
