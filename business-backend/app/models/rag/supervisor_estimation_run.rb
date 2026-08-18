# Session 14 — AR root of the SUPERVISOR flow and its human review inbox.
#
# The service IA owns the state (its Postgres checkpointer, keyed by ``estimation_id``).
# We mirror just enough here to render the current screen and to survive a pause of
# minutes or days.
#
# The difference from Rag::GraphEstimationRun is the shape of the human involvement.
# That wizard pauses at two FIXED gates, so every run walks the same screens. Here the
# gate is CONDITIONAL: most runs complete unattended, and only the ones whose estimate
# is not trustworthy enough land in the inbox. So this model is less a wizard and more
# a work queue — hence ``.awaiting_review`` and the (run_state, status) index.
module Rag
  class SupervisorEstimationRun < ApplicationRecord
    self.table_name = "supervisor_estimation_runs"

    # The single gate the supervisor flow can pause at.
    GATE_REVIEW = "low_confidence_review".freeze
    AWAITING = "awaiting_human_review".freeze

    validates :transcript, presence: true
    validates :estimation_id, presence: true, uniqueness: true

    # The inbox: runs currently waiting for a person, oldest first (a review that has
    # been waiting longest is the most urgent).
    scope :awaiting_review, -> { where(run_state: "paused").order(created_at: :asc) }
    scope :recent, -> { order(created_at: :desc) }

    def paused? = run_state == "paused"
    def completed? = run_state == "completed"
    def awaiting_review? = status == AWAITING

    def rejected? = status == "rejected"
    def validated? = status == "validated"

    # Why the run stopped, in the words the service IA produced.
    def review_reasons = Array(pending_review["reasons"])

    def threshold = pending_review["threshold"]

    def estimate_components = Array(estimate["components"])

    def estimate? = estimate.present? && estimate["components"].present?

    def total_engineer_days = estimate["total_engineer_days"].to_i

    # Session 14 (live) — competition: the synthesized range and the open questions ride
    # INSIDE the estimate JSONB, so they surface with no change to the HTTP contract. A
    # wide low..high bracket is the divergence between the two estimators made visible.
    def estimate_range = estimate.is_a?(Hash) ? estimate["range"] : nil

    def open_questions = Array(estimate.is_a?(Hash) ? estimate["open_questions"] : nil)

    # Confidence as a percentage, for the badge. nil-safe: a run that never reached the
    # validator has no confidence rather than a confidence of zero.
    def confidence_pct = confidence && (confidence * 100).round

    def privilege_violations? = privilege_violations.present?

    # Merge a SupervisorRunState (from the service IA) into this row. ONE mapping used
    # by both start and resume, so the persisted shape cannot drift from the contract.
    # The ``||`` fallbacks mean a later read never nils out an artifact an earlier one
    # already captured.
    def apply_run_state!(run_state_payload)
      payload = run_state_payload.to_h.stringify_keys
      assign_attributes(
        run_state: payload["state"] || "paused",
        status: payload["status"] || status,
        pending_review: payload["pending_review"] || {},
        requirements: payload["requirements"] || requirements,
        components: payload["components"] || components,
        budget_matches: payload["budget_matches"] || budget_matches,
        estimate: payload["estimate"] || estimate,
        validation: payload["validation"] || validation,
        confidence: payload["confidence"] || confidence,
        routing_history: payload["routing_history"] || routing_history,
        agent_contributions: payload["agent_contributions"] || agent_contributions,
        privilege_violations: payload["privilege_violations"] || privilege_violations,
        human_decision: payload["human_decision"] || human_decision,
        # Named errors_list: ``errors`` is reserved by ActiveModel for the validation
        # errors object, and shadowing it breaks validation everywhere.
        errors_list: payload["errors"] || errors_list
      )
      save!
    end
  end
end
