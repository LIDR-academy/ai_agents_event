require "test_helper"

class HomeControllerTest < ActionDispatch::IntegrationTest
  test "root renders the dashboard with one card per context" do
    get root_path

    assert_response :success
    assert_match "Estimación", response.body
    assert_match "Conversación", response.body
    assert_match "RAG Lab", response.body
    # The cards link into each context.
    assert_select "a[href=?]", new_estimation_path
    assert_select "a[href=?]", new_chat_session_path
    assert_select "a[href=?]", new_rag_chunking_comparison_path
  end
end
