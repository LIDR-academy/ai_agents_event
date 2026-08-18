# Mirror of the FastAPI ``CompareResponse`` Pydantic schema: per-strategy
# corpus stats plus (optionally) the top-k retrieval results per query.
module Rag
  class ComparisonResponse
    attr_reader :stats_per_strategy, :queries_per_strategy

    def self.from_hash(hash)
      new(
        stats_per_strategy: hash["stats_per_strategy"].to_h,
        queries_per_strategy: hash["queries_per_strategy"].to_h
      )
    end

    def initialize(stats_per_strategy:, queries_per_strategy:)
      @stats_per_strategy = stats_per_strategy.transform_values do |raw|
        Rag::Stats.new(raw.transform_keys(&:to_s))
      end
      @queries_per_strategy = queries_per_strategy.transform_values do |results|
        Array(results).map { |raw| Rag::QueryResult.new(raw.transform_keys(&:to_s)) }
      end
    end

    # Canonical catalog order, restricted to the strategies present.
    def strategy_names
      Rag::Strategy::ALL_NAMES.select { |name| @stats_per_strategy.key?(name) } |
        @stats_per_strategy.keys
    end

    def stats_for(name) = @stats_per_strategy[name]

    def query_results_for(name) = @queries_per_strategy.fetch(name, [])

    # Unique query strings, in emission order (every strategy ran the same set).
    def queries
      @queries_per_strategy.values.flatten.map(&:query).uniq
    end

    # The top-k list each strategy produced for one given query.
    def results_by_strategy_for(query)
      strategy_names.filter_map do |name|
        result = query_results_for(name).find { |query_result| query_result.query == query }
        [ name, result ] if result
      end
    end

    def any_queries? = @queries_per_strategy.values.any?(&:any?)

    def total_ingestion_cost_usd
      @stats_per_strategy.values.sum { |stats| stats.ingestion_cost_usd.to_f }.round(6)
    end

    def max_ingestion_cost_usd
      @stats_per_strategy.values.map { |stats| stats.ingestion_cost_usd.to_f }.max.to_f
    end
  end
end
