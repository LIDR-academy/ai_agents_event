# Ajustes del servicio IA: overrides de modelo en runtime (Settings UI).
# La página lee/escribe GET/PUT /api/v1/config/models — el cambio aplica en la
# siguiente llamada LLM, sin tocar .env ni recrear contenedores.
class AiSettingsController < ApplicationController
  def show
    @catalog = Ai::Catalog.from_hash(EstimatorAi::ConfigClient.new.get_models)
  rescue EstimatorAi::Error, Faraday::ConnectionFailed, Faraday::TimeoutError => e
    flash.now[:alert] = "No se pudo leer la configuración del servicio IA: #{e.message}"
    @catalog = nil
    render :show, status: :service_unavailable
  end

  def update
    changes = sanitized_changes
    payload = EstimatorAi::ConfigClient.new.update_models(changes)
    expire_primary_model_badge

    @catalog = Ai::Catalog.from_hash(payload)
    redirect_to ai_settings_path, notice: "Modelos actualizados. El cambio aplica en la siguiente llamada."
  rescue EstimatorAi::InvalidRequest => e
    flash.now[:alert] = "Cambio rechazado: #{e.message}"
    reload_catalog_and_render(status: :unprocessable_entity)
  rescue EstimatorAi::Error, Faraday::ConnectionFailed, Faraday::TimeoutError => e
    flash.now[:alert] = "El servicio IA no respondió: #{e.message}"
    reload_catalog_and_render(status: :service_unavailable)
  end

  private

  # Every knob travels on each save; "" means "use the .env default" (reset).
  def sanitized_changes
    raw = params.fetch(:models, {}).permit(*Ai::ModelConfig::KEYS).to_h
    raw.slice(*Ai::ModelConfig::KEYS).transform_values(&:presence)
  end

  # The navbar badge caches the primary model for 15s — drop it so the change
  # shows up immediately after saving.
  def expire_primary_model_badge
    Rails.cache.delete("ai:primary_model")
  end

  def reload_catalog_and_render(status:)
    @catalog = Ai::Catalog.from_hash(EstimatorAi::ConfigClient.new.get_models)
    render :show, status: status
  rescue StandardError
    @catalog = nil
    render :show, status: status
  end
end
