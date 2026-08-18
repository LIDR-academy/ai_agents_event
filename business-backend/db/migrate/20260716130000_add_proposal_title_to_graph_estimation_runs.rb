# Session 13 live — the standalone proposal endpoint returns a full CommercialProposal;
# we persist its LLM-authored title alongside the markdown body so both the on-screen
# proposal and the PDF export can show a proper heading.
class AddProposalTitleToGraphEstimationRuns < ActiveRecord::Migration[8.0]
  def change
    add_column :graph_estimation_runs, :proposal_title, :string
  end
end
