# Context client for the transactional estimation flow (Session 4).
# One endpoint: free-text in, validated structured estimation out.
module EstimatorAi
  class EstimationsClient < BaseClient
    # ``request`` is an Estimation::Request (the Pydantic EstimationRequest mirror).
    def estimate(request)
      raise ArgumentError, "request must be valid" unless request.valid?

      response = json_conn.post("/api/v1/estimate", request.to_payload)
      handle_response(response)
    end
  end
end
