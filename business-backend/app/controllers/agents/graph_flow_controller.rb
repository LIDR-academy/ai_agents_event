# Session 13 — the multi-agent graph flow as a read-only visual resource. Purely
# didactic: it renders the static Agents::GraphFlow catalog (no call to the AI
# service), so the screen works even when the service is down.
module Agents
  class GraphFlowController < ApplicationController
    def show
      @nodes = Agents::GraphFlow::NODES
    end
  end
end
