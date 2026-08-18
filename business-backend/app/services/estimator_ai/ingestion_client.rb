# NOTE: This client is purely ILLUSTRATIVE. The Session 6 endpoints are an HTTP
# contract: any HTTP client (curl, httpx, requests, fetch, ...) reaches them
# equally well. This Rails wrapper exists only so the Master en AI Engineering
# reference stack stays coherent end-to-end — it carries no business logic.
#
# The IA service in Python is autoritative. The mapping table, the catalog and
# the parsers all live there. Rails only triggers an ingestion run and polls
# the resulting job — both endpoints are stateless from Rails' point of view.

module EstimatorAi
  class IngestionClient < BaseClient
    # Ingestion has its own error semantics (catalog decisions, async jobs),
    # so it keeps a local taxonomy instead of the shared handle_response map.
    Error           = Class.new(StandardError)
    UnknownSource   = Class.new(Error)
    NotIncluded     = Class.new(Error)
    JobNotFound     = Class.new(Error)
    ServerError     = Class.new(Error)

    # POST /api/v1/ingestion/runs — kicks off the ingestion. Returns the
    # job_id immediately (HTTP 202). The actual work runs as a FastAPI
    # BackgroundTask in the IA service; the caller polls ``job_status``.
    def trigger_ingestion(source_name:)
      response = json_conn.post("/api/v1/ingestion/runs", { source_name: source_name })
      case response.status
      when 202 then response.body
      when 404 then raise UnknownSource, source_name
      when 400 then raise NotIncluded, response.body.dig("detail", "decision")
      else
        raise ServerError, "unexpected status #{response.status} #{response.body.inspect}"
      end
    end

    # GET /api/v1/ingestion/jobs/{job_id}. Returns the latest snapshot of the
    # job row (status, documents_count, error_message, started_at, finished_at).
    def job_status(job_id)
      response = json_conn.get("/api/v1/ingestion/jobs/#{job_id}")
      case response.status
      when 200 then response.body
      when 404 then raise JobNotFound, job_id
      else
        raise ServerError, "unexpected status #{response.status}"
      end
    end
  end
end
