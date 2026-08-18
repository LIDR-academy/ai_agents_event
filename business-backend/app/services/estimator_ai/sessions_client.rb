# Context client for the conversational flow (Session 5): multi-turn sessions,
# optional attachments, tier override and the Actor-Critic-Boss audited variant.
module EstimatorAi
  class SessionsClient < BaseClient
    def create_session
      response = json_conn.post("/sessions")
      raise ServerError, "unexpected status #{response.status}" unless response.status == 201
      response.body
    end

    def get_session(session_id)
      response = json_conn.get("/sessions/#{session_id}")
      case response.status
      when 200 then response.body
      when 404 then raise SessionNotFound, session_id
      else
        raise ServerError, "unexpected status #{response.status}"
      end
    end

    # ``request`` is a Conversation::Request; ``attachments`` is an array of
    # ActionDispatch::Http::UploadedFile (or anything responding to
    # tempfile/original_filename/content_type). ``tier`` is the optional
    # explicit override consumed by the FastAPI tier resolver
    # (auto/executive/pm/developer/default).
    def estimate_in_session(session_id, request, attachments: [], tier: nil)
      raise ArgumentError, "request must be valid" unless request.valid?
      response = multipart_conn.post(
        "/sessions/#{session_id}/estimate",
        build_estimate_body(request, attachments, tier)
      )
      handle_response(response)
    end

    # ACB variant: same contract; the response carries an ``acb`` field with
    # the iteration trail. Surfaces a richer view of how the estimation was
    # produced (verdict + issues per iteration).
    def estimate_in_session_acb(session_id, request, attachments: [], tier: nil)
      raise ArgumentError, "request must be valid" unless request.valid?
      response = multipart_conn.post(
        "/sessions/#{session_id}/estimate-acb",
        build_estimate_body(request, attachments, tier)
      )
      handle_response(response)
    end

    private

    def build_estimate_body(request, attachments, tier)
      body = {
        "transcript"    => request.transcript,
        "project_type"  => request.project_type,
        "detail_level"  => request.detail_level,
        "output_format" => request.output_format
      }
      # ``tier`` is opt-in; FastAPI treats omission as "auto-derive".
      body["tier"] = tier if tier.present? && tier != "auto"

      Array(attachments).compact.each do |file|
        body["attachments"] = [] unless body["attachments"].is_a?(Array)
        body["attachments"] << Faraday::Multipart::FilePart.new(
          file.tempfile,
          file.content_type || "application/octet-stream",
          file.original_filename
        )
      end
      body
    end
  end
end
