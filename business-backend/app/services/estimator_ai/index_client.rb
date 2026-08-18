# Session 11 — corpus expansion client. Wraps the FastAPI endpoints that add new
# information to the vector DB and report progress:
#   POST /embeddings/index/runs      → 202 + job_id (async BackgroundTask)
#   GET  /embeddings/index/jobs/{id} → progress snapshot
#   GET  /embeddings/index/stats     → per-collection corpus size + index state
#
# Like IngestionClient, indexing has its own async-job semantics, so it keeps a
# local error taxonomy rather than the shared handle_response map. The IA service
# is authoritative; Rails only triggers the run and polls it.
module EstimatorAi
  class IndexClient < BaseClient
    Error        = Class.new(StandardError)
    InvalidBatch = Class.new(Error)
    JobNotFound  = Class.new(Error)
    ServerError  = Class.new(Error)

    # POST /embeddings/index/runs — kicks off the expansion. ``documents`` is an
    # array of Budget hashes. Returns { job_id, documents_total, status } (202).
    def start_expansion(documents:, chunk_type: "budget_component", document_type: "historical_budget")
      response = json_conn.post("/embeddings/index/runs", {
        documents: documents,
        chunk_type: chunk_type,
        document_type: document_type
      })
      case response.status
      when 202 then response.body
      when 422 then raise InvalidBatch, response.body.to_s
      when 500 then raise ServerError, response.body.dig("detail").to_s
      else
        raise ServerError, "unexpected status #{response.status} #{response.body.inspect}"
      end
    end

    # GET /embeddings/index/jobs/{job_id} — progress snapshot.
    def job_status(job_id)
      response = json_conn.get("/embeddings/index/jobs/#{job_id}")
      case response.status
      when 200 then response.body
      when 404 then raise JobNotFound, job_id
      else
        raise ServerError, "unexpected status #{response.status}"
      end
    end

    # GET /embeddings/index/stats — per-collection corpus size + HNSW index state.
    def corpus_stats
      response = json_conn.get("/embeddings/index/stats")
      case response.status
      when 200 then response.body
      else
        raise ServerError, "unexpected status #{response.status}"
      end
    end
  end
end
