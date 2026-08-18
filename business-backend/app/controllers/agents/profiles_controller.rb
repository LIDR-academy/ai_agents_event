# Session 12 — the agents console. CRUD over named profiles for the hand-written
# agent; the ACB is presented read-only in the index. Model dropdowns are fed by
# the AI service's live `available_models` (falls back to a static list if the
# service is unreachable, so the screen still renders offline).
module Agents
  class ProfilesController < ApplicationController
    FALLBACK_MODELS = %w[gpt-5 gpt-5-mini gpt-4o gpt-4o-mini].freeze

    def index
      @profiles = Agents::Profile.order(:agent_type, :name)
    end

    def show
      @profile = Agents::Profile.find(params[:id])
    end

    def new
      @profile = Agents::Profile.new(agent_type: "handwritten")
      load_available_models
    end

    def edit
      @profile = Agents::Profile.find(params[:id])
      load_available_models
    end

    def create
      @profile = Agents::Profile.new(name: profile_params[:name],
                                     agent_type: "handwritten",
                                     persona: profile_params[:persona],
                                     is_default: profile_params[:is_default] == "1")
      @profile.assign_config(profile_params[:config])
      attach_avatar
      if @profile.save
        redirect_to agents_profiles_path, notice: "Perfil «#{@profile.name}» creado."
      else
        load_available_models
        render :new, status: :unprocessable_entity
      end
    end

    def update
      @profile = Agents::Profile.find(params[:id])
      @profile.assign_attributes(name: profile_params[:name],
                                 persona: profile_params[:persona],
                                 is_default: profile_params[:is_default] == "1")
      @profile.assign_config(profile_params[:config])
      attach_avatar
      if @profile.save
        redirect_to agents_profiles_path, notice: "Perfil «#{@profile.name}» actualizado."
      else
        load_available_models
        render :edit, status: :unprocessable_entity
      end
    end

    def destroy
      @profile = Agents::Profile.find(params[:id])
      @profile.destroy
      redirect_to agents_profiles_path, notice: "Perfil «#{@profile.name}» eliminado."
    end

    private

    def profile_params
      params.require(:profile).permit(:name, :persona, :is_default, :avatar,
                                      config: Agents::Profile::CONFIG_KEYS)
    end

    # Attach the uploaded avatar only when a new file arrives, so editing a profile
    # without re-uploading keeps the existing picture instead of purging it.
    def attach_avatar
      file = profile_params[:avatar]
      @profile.avatar.attach(file) if file.present?
    end

    # The model dropdown source: the AI service's live catalog, degrading to a
    # static list when the service is down so the form still renders.
    def load_available_models
      payload = EstimatorAi::ConfigClient.new.get_models
      @available_models = Array(payload["available_models"]).presence || FALLBACK_MODELS
    rescue EstimatorAi::Error, Faraday::Error
      @available_models = FALLBACK_MODELS
    end
  end
end
