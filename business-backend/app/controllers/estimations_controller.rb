class EstimationsController < ApplicationController
  def index
    @estimations = Estimation.order(created_at: :desc).limit(20)
  end

  def new
    @request = Estimation::Request.new
  end

  def create
    @request = Estimation::Request.new(estimation_request_params)

    unless @request.valid?
      render :new, status: :unprocessable_entity
      return
    end

    payload = EstimatorAi::EstimationsClient.new.estimate(@request)

    @estimation = Estimation.create!(
      description:      @request.description,
      project_type:     @request.project_type,
      detail_level:     @request.detail_level,
      output_format:    @request.output_format,
      response_payload: payload,
      prompt_version:   payload["prompt_version"],
      cached:           payload["cached"] || false
    )

    redirect_to estimation_path(@estimation)
  rescue EstimatorAi::GuardrailViolation => e
    flash.now[:alert] = e.message
    render :new, status: :unprocessable_entity
  rescue EstimatorAi::InvalidRequest => e
    flash.now[:alert] = e.message
    render :new, status: :unprocessable_entity
  rescue EstimatorAi::ServerError, Faraday::ConnectionFailed, Faraday::TimeoutError => e
    flash.now[:alert] = "AI service unavailable: #{e.message}"
    render :new, status: :service_unavailable
  end

  def show
    @estimation = Estimation.find(params[:id])
    @response   = @estimation.to_response
  end

  private

  def estimation_request_params
    params.require(:estimation_request).permit(
      :description, :project_type, :detail_level, :output_format
    )
  end
end
