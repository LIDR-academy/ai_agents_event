# Mirror of the FastAPI ``TopChunk`` Pydantic schema: one ranked chunk of a
# playground query.
#
# ``level`` is derived from the chunk_id naming of the hierarchical strategy
# ("BUD-X::parent" → parent, "BUD-X::COMP" → child): the compare response
# strips chunk metadata, so this is the only parent/child signal available
# client-side. Only meaningful when the strategy is "hierarchical".
module Rag
  class TopChunk
    include ActiveModel::Model
    include ActiveModel::Attributes

    PARENT_SUFFIX = "::parent".freeze

    attribute :chunk_id, :string
    attribute :cosine, :float
    attribute :text_preview, :string

    def level
      return nil unless chunk_id.to_s.include?("::")
      chunk_id.end_with?(PARENT_SUFFIX) ? :parent : :child
    end

    def parent_label
      chunk_id.to_s.split("::").first
    end

    def cosine_pct
      (cosine.to_f.clamp(0.0, 1.0) * 100).round
    end
  end
end
