# Mirror of the FastAPI ``EstimationResponse`` Pydantic schema: the typed
# wrapper the views render instead of raw JSON.
class Estimation::Response
  include ActiveModel::Model
  include ActiveModel::Attributes

  attribute :result               # Estimation::Result instance
  attribute :prompt_version, :string
  attribute :cached, :boolean, default: false

  def self.from_hash(hash)
    new(
      result: Estimation::Result.new(hash["result"].to_h),
      prompt_version: hash["prompt_version"],
      cached: hash["cached"] || false
    )
  end
end
