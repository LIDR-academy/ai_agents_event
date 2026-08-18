# Mirror of the FastAPI ``WorkModule``: a functional block grouping the concrete
# tasks needed to deliver it.
module Rag
  class WorkModuleView
    include ActiveModel::Model
    include ActiveModel::Attributes

    attribute :name, :string
    attribute :description, :string

    attr_reader :tasks

    def self.from_hash(hash)
      new(hash || {})
    end

    def initialize(attributes = {})
      stringified = (attributes || {}).transform_keys(&:to_s)
      @tasks = Array(stringified.delete("tasks")).map { |raw| Rag::TaskItemView.from_hash(raw) }
      super(stringified.slice("name", "description"))
    end

    def subtotal = tasks.sum { |task| task.engineer_days.to_i }

    # Session 10 hours flow subtotals. ``to_f`` for the same reason TaskItemView declares
    # the attribute as a float: half hours are real and must not be dropped.
    def subtotal_hours = tasks.sum { |task| task.estimated_hours.to_f }

    def subtotal_cost = tasks.sum(&:cost_eur)
  end
end
