# Static corpus shipped with the client: a copy of the estimator's 17-budget
# sample plus the six canned benchmark queries. The budgets travel verbatim in
# the compare request body; FastAPI validates them with Pydantic.
#
# Kept under lib/estimator_ai/data/ (static reference data, not code, not
# DB-seeded). If the estimator's sample evolves, re-copy both files.
module EstimatorAi
  class BudgetCorpus
    LABEL = "budgets_sample".freeze
    DATA_DIR = Rails.root.join("lib", "estimator_ai", "data")

    def self.budgets
      @budgets ||= JSON.parse(File.read(DATA_DIR.join("budgets_sample.json")))
    end

    def self.sample_queries
      @sample_queries ||= JSON.parse(File.read(DATA_DIR.join("test_queries.json")))
    end
  end
end
