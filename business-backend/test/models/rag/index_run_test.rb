require "test_helper"

class RagIndexRunTest < ActiveSupport::TestCase
  def stats(total, budget_chunks)
    {
      "total_chunks" => total,
      "collections" => [
        { "collection" => "budget", "documents" => 10, "chunks" => budget_chunks, "hnsw_indexed" => true }
      ]
    }
  end

  test "finished? is true only for terminal statuses" do
    assert Rag::IndexRun.new(status: "completed").finished?
    assert Rag::IndexRun.new(status: "failed").finished?
    assert_not Rag::IndexRun.new(status: "running").finished?
  end

  test "chunks_added is the after − before delta once both stats are known" do
    run = Rag::IndexRun.new(before_stats: stats(64, 64), after_stats: stats(70, 70))
    assert_equal 6, run.chunks_added
  end

  test "chunks_added is nil until after_stats are captured" do
    run = Rag::IndexRun.new(before_stats: stats(64, 64), after_stats: {})
    assert_nil run.chunks_added
  end

  test "collection stats expose typed fields" do
    run = Rag::IndexRun.new(after_stats: stats(70, 70))
    c = run.after_collections.first
    assert_equal "budget", c.collection
    assert_equal 70, c.chunks
    assert c.hnsw_indexed?
  end
end
