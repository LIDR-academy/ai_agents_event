# A named, personalizable preset for an agent (Session 12 console). Today only the
# hand-written S12 agent is profile-driven; the ACB is shown read-only in the UI.
#
# The knobs live in the JSONB `config` bag so the schema does not churn as the
# agent gains parameters. `config_payload` is exactly the override body POSTed to
# the AI service (nils compacted, so an unset knob falls back to the service
# default). `persona` is free text appended to the agent's system prompt.
module Agents
  class Profile < ApplicationRecord
    self.table_name = "agent_profiles"

    AGENT_TYPES = %w[handwritten].freeze
    EFFORTS = %w[minimal low medium high].freeze
    # The knob keys stored in `config` (also the strong-params whitelist).
    CONFIG_KEYS = %w[
      model reasoning_effort max_iterations search_top_k search_distance_threshold
    ].freeze
    # Accepted avatar content types (stored locally via Active Storage disk service).
    AVATAR_CONTENT_TYPES = %w[image/png image/jpeg image/gif image/webp].freeze

    # Profile picture, kept on the local disk service (see config/storage.yml).
    has_one_attached :avatar

    validates :name, presence: true, uniqueness: { scope: :agent_type, case_sensitive: false }
    validate :avatar_is_an_image
    validates :agent_type, inclusion: { in: AGENT_TYPES }
    validates :persona, length: { maximum: 2000 }
    validates :reasoning_effort, inclusion: { in: EFFORTS }, allow_blank: true
    validates :max_iterations, numericality: { only_integer: true, greater_than_or_equal_to: 1,
                                               less_than_or_equal_to: 20 }, allow_nil: true
    validates :search_top_k, numericality: { only_integer: true, greater_than_or_equal_to: 1,
                                             less_than_or_equal_to: 30 }, allow_nil: true
    validates :search_distance_threshold, numericality: { greater_than_or_equal_to: 0,
                                                          less_than_or_equal_to: 2 }, allow_nil: true

    before_save :ensure_single_default

    # --- typed accessors over the JSONB config bag ---------------------------

    def model = config["model"].presence

    def reasoning_effort = config["reasoning_effort"].presence

    def max_iterations = config["max_iterations"]

    def search_top_k = config["search_top_k"]

    def search_distance_threshold = config["search_distance_threshold"]

    # The override body sent to POST /v1/estimate/agent/run: only the set knobs,
    # coerced to their types. An absent knob is omitted → the AI service default.
    def config_payload
      {
        "model" => model,
        "reasoning_effort" => reasoning_effort,
        "max_iterations" => max_iterations&.to_i,
        "search_top_k" => search_top_k&.to_i,
        "search_distance_threshold" => search_distance_threshold&.to_f
      }.compact
    end

    # Assign the knob subset from a permitted params hash, dropping blanks so an
    # empty field means "use the service default" rather than an invalid override.
    def assign_config(raw)
      cleaned = (raw || {}).to_h.slice(*CONFIG_KEYS).transform_values { |v| v.to_s.strip.presence }
      self.config = cleaned.compact
    end

    private

    def ensure_single_default
      return unless is_default?

      Agents::Profile.where(agent_type: agent_type).where.not(id: id).update_all(is_default: false)
    end

    # Active Storage does not validate content type on its own — reject anything
    # that is not one of our accepted image formats so a stray upload never sticks.
    def avatar_is_an_image
      return unless avatar.attached?
      return if AVATAR_CONTENT_TYPES.include?(avatar.blob.content_type)

      errors.add(:avatar, "must be a PNG, JPEG, GIF or WEBP image")
    end
  end
end
