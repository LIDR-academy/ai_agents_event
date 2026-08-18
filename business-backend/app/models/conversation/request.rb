# Form-backed mirror of the conversational /sessions/:id/estimate contract
# (Session 5). Same enums as the transactional request; ``description`` becomes
# ``transcript`` to match the FastAPI multipart contract.
module Conversation
  class Request
    include ActiveModel::Model
    include ActiveModel::Attributes

    PROJECT_TYPES  = Estimation::Request::PROJECT_TYPES
    DETAIL_LEVELS  = Estimation::Request::DETAIL_LEVELS
    OUTPUT_FORMATS = Estimation::Request::OUTPUT_FORMATS

    attribute :transcript,    :string
    attribute :project_type,  :string
    attribute :detail_level,  :string, default: "medium"
    attribute :output_format, :string, default: "phases_table"

    validates :transcript,    presence: true, length: { in: 20..80000 }
    validates :project_type,  presence: true, inclusion: { in: PROJECT_TYPES }
    validates :detail_level,  inclusion: { in: DETAIL_LEVELS }
    validates :output_format, inclusion: { in: OUTPUT_FORMATS }
  end
end
