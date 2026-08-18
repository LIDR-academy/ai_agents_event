require "prawn"
require "prawn/table"

# Built-in AFM fonts are intentional (basic PDF, no bundled TTF); silence the m17n note.
Prawn::Fonts::AFM.hide_m17n_warning = true

module Rag
  # A self-contained PDF of the commercial proposal for a completed graph run. Pure Ruby
  # (Prawn), no system binary. Composes: title, budget (module rollup + rate/contingency),
  # per-task detail grouped by module, and the proposal body (the LLM markdown rendered as
  # plain paragraphs).
  #
  # NO reliability section: this is a client-facing document, and how much historical
  # precedent the estimate had is an internal matter — it stays on the result screen.
  # The tables come from Rag::BudgetBreakdown, the same object that builds the markdown
  # ones, so the two documents cannot show different figures. Text is sanitised to WinAnsi because
  # Prawn's built-in fonts only cover that range (LLM output carries smart quotes,
  # em dashes, bullets and the occasional emoji that would otherwise raise).
  class ProposalPdf
    BRAND = "999900".freeze # muted gold, matches the app accent in print

    def initialize(run)
      @run = run
    end

    def render
      pdf = Prawn::Document.new(page_size: "A4", margin: 48)
      heading(pdf)
      body(pdf)
      budget(pdf)
      task_detail(pdf)
      pdf.render
    end

    private

    def heading(pdf)
      pdf.text clean(@run.proposal_title.presence || "Propuesta comercial"), size: 20, style: :bold
      pdf.fill_color "888888"
      pdf.text "Estimacion por grafo de agentes  -  run ##{@run.id}", size: 9
      pdf.fill_color "000000"
      pdf.move_down 16
    end

    # The budget: module rollup with a TOTAL row, then the rate/contingency detail. Rows
    # come from Rag::BudgetBreakdown so this table and the markdown one cannot disagree.
    def budget(pdf)
      breakdown = @run.budget
      return unless breakdown.any?

      pricing = breakdown.pricing
      section(pdf, "Presupuesto")
      headline = "Total: #{@run.total_engineer_days} jornadas  -  #{Rag::Hours.format(@run.total_engineer_hours)} h"
      headline += "  -  #{euros(pricing.total)}" if pricing.priced?
      pdf.text clean(headline), size: 11, style: :bold
      pdf.move_down 6

      headers = pricing.priced? ? [ "Modulo", "Tareas", "Horas", "Coste" ] : [ "Modulo", "Tareas", "Horas" ]
      rows = [ headers ]
      breakdown.module_rows.each { |r| rows << summary_row(r, pricing) }
      rows << summary_row(breakdown.total_row, pricing)

      pdf.table(rows, header: true, width: pdf.bounds.width, cell_style: { size: 9, padding: 5 }) do |t|
        t.row(0).font_style = :bold
        t.row(0).background_color = "EEEEEE"
        t.row(rows.size - 1).font_style = :bold
        t.row(rows.size - 1).background_color = "F5F5F5"
        t.columns(1..(headers.size - 1)).align = :right
      end

      if pricing.priced?
        pdf.move_down 6
        detail = "Base #{euros(pricing.base)} (#{Rag::Hours.format(pricing.hours)} h x #{pricing.rate_eur_per_hour} EUR/h)"
        detail += "  +  contingencia #{pricing.contingency_pct}% #{euros(pricing.contingency)}" if pricing.contingency?
        detail += "  =  #{euros(pricing.total)}"
        pdf.text clean(detail), size: 9
      end
      pdf.move_down 16
    end

    def summary_row(row, pricing)
      cells = [ clean(row.label), row.count.to_s, "#{Rag::Hours.format(row.hours)} h" ]
      cells << euros(row.cost) if pricing.priced?
      cells
    end

    # Every task, grouped under its module, so the client can audit where the hours went.
    def task_detail(pdf)
      breakdown = @run.budget
      return unless breakdown.any?

      priced = breakdown.priced?
      section(pdf, "Detalle por tarea")
      headers = priced ? [ "Tarea", "Horas", "Coste" ] : [ "Tarea", "Horas" ]
      # The task column takes whatever the numeric ones do not: names run long.
      numeric = priced ? 200 : 100
      widths = [ pdf.bounds.width - numeric ] + (priced ? [ 100, 100 ] : [ 100 ])

      breakdown.task_groups.each do |name, rows|
        next if rows.empty?

        pdf.text clean(name), size: 10, style: :bold
        pdf.move_down 3
        table = [ headers ] + rows.map { |r|
          cells = [ clean(r.label), "#{Rag::Hours.format(r.hours)} h" ]
          cells << euros(r.cost) if priced
          cells
        }
        pdf.table(table, header: true, column_widths: widths,
                         cell_style: { size: 8, padding: 4 }) do |t|
          t.row(0).font_style = :bold
          t.row(0).background_color = "EEEEEE"
          t.columns(1..(headers.size - 1)).align = :right
        end
        pdf.move_down 10
      end
      pdf.move_down 6
    end

    # Whole euros, with the sign spelled out: the PDF font is WinAnsi and "EUR" travels
    # everywhere, which a currency glyph does not.
    def euros(amount) = "#{amount.round.to_s.reverse.scan(/\d{1,3}/).join('.').reverse} EUR"

    # The proposal prose. Line-oriented on purpose.
    #
    # The previous version split on blank lines and matched a heading with /\A#+\s*(.+)/ —
    # no /m flag, so it captured only the FIRST LINE and silently threw away the rest of the
    # block. The model does not leave a blank line after its headings, so heading and
    # paragraph arrive together: every proposal rendered as a list of bare titles with the
    # body text missing (451 characters lost under "Executive Summary" alone).
    def body(pdf)
      prose = @run.proposal_prose
      return if prose.blank?

      section(pdf, "Propuesta")
      paragraph = []
      prose.each_line do |raw|
        line = raw.rstrip
        case line
        when /\A\s*\z/
          flush(pdf, paragraph)
        when /\A(#+)\s*(.+)\z/
          flush(pdf, paragraph)
          level = Regexp.last_match(1).length
          pdf.move_down 6
          pdf.text clean(strip_emphasis(Regexp.last_match(2))),
                   size: level <= 2 ? 13 : 11, style: :bold
          pdf.move_down 3
        when /\A\s*[-*]\s+(.+)\z/
          flush(pdf, paragraph)
          pdf.text clean("-  #{strip_emphasis(Regexp.last_match(1))}"), size: 10
        else
          paragraph << line.strip
        end
      end
      flush(pdf, paragraph)
      pdf.move_down 10
    end

    def flush(pdf, paragraph)
      return if paragraph.empty?

      pdf.text clean(strip_emphasis(paragraph.join(" "))), size: 10, align: :justify
      pdf.move_down 6
      paragraph.clear
    end

    # Prawn is not running with inline_format (module names carry "&", which would have to
    # be escaped), so markdown emphasis would print as literal asterisks. Drop the markers.
    def strip_emphasis(text)
      text.gsub(/\*\*(.+?)\*\*/, '\\1').gsub(/(?<!\w)[*_](\S(?:.*?\S)?)[*_](?!\w)/, '\\1')
    end

    def section(pdf, title)
      pdf.fill_color BRAND
      pdf.text clean(title.upcase), size: 10, style: :bold
      pdf.fill_color "000000"
      pdf.stroke_color "DDDDDD"
      pdf.stroke_horizontal_rule
      pdf.stroke_color "000000"
      pdf.move_down 8
    end

    # Prawn's built-in fonts are WinAnsi only. Map the common LLM/Spanish punctuation to
    # ASCII, then drop anything still outside WinAnsi (emoji, box chars) instead of raising.
    def clean(text)
      text.to_s
          .tr("’‘“”", "''\"\"")
          .gsub(/[—–]/, "-")
          .gsub("…", "...")
          .gsub(/[•·]/, "-")
          .encode("Windows-1252", undef: :replace, invalid: :replace, replace: "")
          .encode("UTF-8")
    end
  end
end
