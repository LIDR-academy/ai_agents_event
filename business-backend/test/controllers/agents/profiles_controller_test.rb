require "test_helper"

# Session 12 — the agents console CRUD. The model-catalog GET is stubbed by the
# test_helper default; here we register a richer one so the dropdown reflects the
# live available_models.
class AgentsProfilesControllerTest < ActionDispatch::IntegrationTest
  setup do
    stub_request(:get, %r{/api/v1/config/models})
      .to_return(
        status: 200,
        body: { models: {}, available_models: %w[gpt-5 gpt-5-mini gpt-4o-mini] }.to_json,
        headers: { "Content-Type" => "application/json" }
      )
  end

  test "index lists profiles and shows both agent cards" do
    Agents::Profile.create!(name: "Estándar", is_default: true, config: { "model" => "gpt-5" })
    get agents_profiles_path
    assert_response :success
    assert_match "Agente hecho a mano", response.body
    assert_match "Actor-Critic-Boss", response.body
    assert_match "Estándar", response.body
    # The screens carry no Master-course session tags — they are shown at public events.
    assert_no_match(/\(S\d+\)|S4\/S5|Sesión \d/, response.body)
  end

  test "show renders the profile knobs and persona" do
    profile = Agents::Profile.create!(
      name: "Exhaustivo", persona: "Sobreestima ante ambigüedad.",
      config: { "model" => "gpt-5", "reasoning_effort" => "high", "search_top_k" => "8" }
    )
    get agents_profile_path(profile)
    assert_response :success
    assert_match "Exhaustivo", response.body
    assert_match "gpt-5", response.body
    assert_match "Sobreestima ante ambigüedad.", response.body
  end

  test "new renders the form with the live model catalog" do
    get new_agents_profile_path
    assert_response :success
    assert_select "select[name='profile[config][model]']"
    # The name field must post under profile[name] (scope: :profile), not the
    # namespaced default agents_profile[name] — otherwise create loses the name.
    assert_select "input[name='profile[name]']"
    assert_select "input[name='profile[avatar]'][type='file']"
    assert_match "gpt-5-mini", response.body
  end

  test "create persists a profile with its config bag and persona" do
    assert_difference -> { Agents::Profile.count }, 1 do
      post agents_profiles_path, params: {
        profile: {
          name: "Exhaustivo", persona: "Sobreestima ante ambigüedad.", is_default: "1",
          config: { model: "gpt-5", reasoning_effort: "high", max_iterations: "15",
                    search_top_k: "8", search_distance_threshold: "" }
        }
      }
    end
    profile = Agents::Profile.order(:id).last
    assert_redirected_to agents_profiles_path
    assert_equal "gpt-5", profile.model
    assert_equal 15, profile.config_payload["max_iterations"]
    assert_not profile.config_payload.key?("search_distance_threshold") # blank dropped
    assert profile.is_default
  end

  test "create with a blank name re-renders unprocessable" do
    assert_no_difference -> { Agents::Profile.count } do
      post agents_profiles_path, params: { profile: { name: "", config: { model: "gpt-5" } } }
    end
    assert_response :unprocessable_entity
  end

  test "create attaches an uploaded avatar" do
    post agents_profiles_path, params: {
      profile: {
        name: "Con foto", config: { model: "gpt-5" },
        avatar: fixture_file_upload("avatar.png", "image/png")
      }
    }
    profile = Agents::Profile.order(:id).last
    assert_redirected_to agents_profiles_path
    assert profile.avatar.attached?
    assert_equal "avatar.png", profile.avatar.filename.to_s
  end

  test "update without a new avatar keeps the existing one" do
    profile = Agents::Profile.create!(name: "Base", config: { "model" => "gpt-5" })
    profile.avatar.attach(fixture_file_upload("avatar.png", "image/png"))
    assert profile.avatar.attached?

    patch agents_profile_path(profile), params: {
      profile: { name: "Base", persona: "sin nueva imagen", config: { model: "gpt-5-mini" } }
    }
    assert_redirected_to agents_profiles_path
    profile.reload
    assert profile.avatar.attached?, "editing without re-uploading should keep the avatar"
    assert_equal "gpt-5-mini", profile.model
  end

  test "update edits knobs and persona" do
    profile = Agents::Profile.create!(name: "Base", config: { "model" => "gpt-5" })
    patch agents_profile_path(profile), params: {
      profile: { name: "Base", persona: "Nueva persona", config: { model: "gpt-5-mini",
                 reasoning_effort: "low" } }
    }
    assert_redirected_to agents_profiles_path
    profile.reload
    assert_equal "gpt-5-mini", profile.model
    assert_equal "Nueva persona", profile.persona
  end

  test "destroy removes the profile" do
    profile = Agents::Profile.create!(name: "Temp", config: {})
    assert_difference -> { Agents::Profile.count }, -1 do
      delete agents_profile_path(profile)
    end
    assert_redirected_to agents_profiles_path
  end
end
