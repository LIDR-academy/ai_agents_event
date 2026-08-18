# A conversational session anchored to a remote (FastAPI) session_id.
# The full message history lives in the FastAPI process memory; here we keep
# the metadata snapshot of the latest turn (for the side panel) and the count
# of turns so the UI can show "turn N of N" when useful.
class ChatSession < ApplicationRecord
  has_many :estimations, dependent: :nullify

  validates :remote_session_id, presence: true, uniqueness: true

  def latest_metadata_hash
    latest_metadata.is_a?(Hash) ? latest_metadata : {}
  end

  # Snapshot of the GET /sessions/:id response (tier, anchors, summary
  # chars). Used by the side panel.
  def remote_session_snapshot
    runtime_snapshot.is_a?(Hash) ? runtime_snapshot : {}
  end
end
