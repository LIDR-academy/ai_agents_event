# Conversational estimator UI (Session 5).
#
# A Rails ChatSession mirrors the FastAPI session_id. The full message
# history stays in the FastAPI process; here we persist the latest payload
# (so the user can refresh /chat_sessions/:id and re-read the result) and the
# latest project_metadata snapshot for the side panel.
class ChatSessionsController < ApplicationController
  def new
    @chat_session = current_chat_session || begin
      remote = EstimatorAi::SessionsClient.new.create_session
      ChatSession.create!(remote_session_id: remote["session_id"])
    end
    session[:current_chat_session_id] = @chat_session.id
    @request = Conversation::Request.new
    @latest_estimation = @chat_session.estimations.order(created_at: :desc).first
    @latest_metadata = @chat_session.latest_metadata_hash
  rescue EstimatorAi::ServerError, Faraday::ConnectionFailed, Faraday::TimeoutError => e
    flash.now[:alert] = "AI service unavailable: #{e.message}"
    @chat_session = ChatSession.new
    @request = Conversation::Request.new
    @latest_estimation = nil
    @latest_metadata = {}
    render :new, status: :service_unavailable
  end

  def create
    @chat_session = ChatSession.find(params[:id])
    @request = Conversation::Request.new(conversation_request_params)

    unless @request.valid?
      @latest_estimation = @chat_session.estimations.order(created_at: :desc).first
      @latest_metadata = @chat_session.latest_metadata_hash
      render :new, status: :unprocessable_entity
      return
    end

    attachments = Array(params[:attachments]).compact_blank
    tier = params[:tier].presence
    mode = params[:mode].to_s == "acb" ? "acb" : "actor"
    client = EstimatorAi::SessionsClient.new
    payload =
      if mode == "acb"
        client.estimate_in_session_acb(
          @chat_session.remote_session_id, @request,
          attachments: attachments, tier: tier
        )
      else
        client.estimate_in_session(
          @chat_session.remote_session_id, @request,
          attachments: attachments, tier: tier
        )
      end

    @estimation = @chat_session.estimations.create!(
      description:      @request.transcript,
      project_type:     @request.project_type,
      detail_level:     @request.detail_level,
      output_format:    @request.output_format,
      response_payload: payload,
      prompt_version:   payload["prompt_version"],
      cached:           payload["cached"] || false
    )

    # Refresh the metadata snapshot from FastAPI for the side panel.
    refresh_metadata_snapshot(@chat_session)

    redirect_to chat_session_path(@chat_session)
  rescue EstimatorAi::GuardrailViolation => e
    flash.now[:alert] = e.message
    @request = Conversation::Request.new(conversation_request_params)
    @latest_estimation = @chat_session.estimations.order(created_at: :desc).first
    @latest_metadata = @chat_session.latest_metadata_hash
    render :new, status: :unprocessable_entity
  rescue EstimatorAi::InvalidRequest => e
    flash.now[:alert] = e.message
    @request = Conversation::Request.new(conversation_request_params)
    @latest_estimation = @chat_session.estimations.order(created_at: :desc).first
    @latest_metadata = @chat_session.latest_metadata_hash
    render :new, status: :unprocessable_entity
  rescue EstimatorAi::SessionNotFound
    # The FastAPI process restarted and lost the session_id. Wipe our
    # mirror and bounce the user back to a fresh conversation.
    @chat_session.destroy
    session.delete(:current_chat_session_id)
    flash[:alert] = "Conversational session expired (FastAPI restart). Started a new one."
    redirect_to new_chat_session_path
  rescue EstimatorAi::ServerError, Faraday::ConnectionFailed, Faraday::TimeoutError => e
    flash.now[:alert] = "AI service unavailable: #{e.message}"
    @request = Conversation::Request.new(conversation_request_params)
    @latest_estimation = @chat_session.estimations.order(created_at: :desc).first
    @latest_metadata = @chat_session.latest_metadata_hash
    render :new, status: :service_unavailable
  end

  def show
    @chat_session = ChatSession.find(params[:id])
    @request = Conversation::Request.new
    @latest_estimation = @chat_session.estimations.order(created_at: :desc).first
    @latest_metadata = @chat_session.latest_metadata_hash
    session[:current_chat_session_id] = @chat_session.id
    render :new
  end

  def destroy
    ChatSession.find_by(id: params[:id])&.destroy
    session.delete(:current_chat_session_id)
    redirect_to new_chat_session_path, notice: "Started a new conversation."
  end

  private

  def current_chat_session
    id = session[:current_chat_session_id]
    return nil unless id
    ChatSession.find_by(id: id)
  end

  def conversation_request_params
    params.require(:conversation_request).permit(
      :transcript, :project_type, :detail_level, :output_format
    )
  end

  def refresh_metadata_snapshot(chat_session)
    info = EstimatorAi::SessionsClient.new.get_session(chat_session.remote_session_id)
    chat_session.update!(
      latest_metadata: info["metadata"] || {},
      runtime_snapshot: info.except("metadata") || {},
      turn_count: (info["message_count"] || 0) / 2
    )
  rescue StandardError => e
    Rails.logger.warn("Failed to refresh metadata snapshot: #{e.message}")
  end
end
