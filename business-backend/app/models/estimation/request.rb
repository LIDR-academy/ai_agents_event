# Form-backed mirror of the FastAPI ``EstimationRequest`` Pydantic schema
# (transactional flow, Session 4). The enums are the API contract — changing
# them here without changing the service breaks the 422 validation.
class Estimation::Request
  include ActiveModel::Model
  include ActiveModel::Attributes

  PROJECT_TYPES  = %w[mobile_app web_saas internal_tool data_pipeline].freeze
  DETAIL_LEVELS  = %w[summary medium detailed].freeze
  OUTPUT_FORMATS = %w[phases_table line_items narrative].freeze

  attribute :description,   :string
  attribute :project_type,  :string
  attribute :detail_level,  :string, default: "medium"
  attribute :output_format, :string, default: "phases_table"

  validates :description,   presence: true, length: { in: 20..80000 }
  validates :project_type,  presence: true, inclusion: { in: PROJECT_TYPES }
  validates :detail_level,  inclusion: { in: DETAIL_LEVELS }
  validates :output_format, inclusion: { in: OUTPUT_FORMATS }

  def to_payload
    attributes.symbolize_keys
  end
end
