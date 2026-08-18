module Rag
  # Hours are floats (the graph produces halves: a task can be 7.5 h), but nobody wants to
  # read "152.0 h" on a slide. One formatter, shared by the views and the PDF service, so
  # the same number never renders two different ways in the same run.
  module Hours
    def self.format(value)
      return nil if value.nil?

      hours = value.to_f.round(1)
      hours == hours.round ? hours.round.to_s : hours.to_s
    end
  end
end
