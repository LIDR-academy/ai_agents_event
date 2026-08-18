# ActiveRecord root of the transactional-estimation context. Persists each
# FastAPI call (full payload as JSONB); its contract mirrors live underneath
# as Estimation::Request/Response/Result/Phase.
class Estimation < ApplicationRecord
  belongs_to :chat_session, optional: true

  validates :description, :project_type, :detail_level, :output_format, presence: true

  def to_response
    Estimation::Response.from_hash(response_payload)
  end

  def description_preview(limit: 80)
    description.to_s.truncate(limit)
  end
end
