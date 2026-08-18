module Rag
  # Turns grounded hours into money. Deterministic arithmetic, computed HERE — never by a
  # model. The AI service is only ever handed the resulting figures to quote.
  #
  #   base         = hours × rate
  #   contingency  = base × pct / 100
  #   total        = base + contingency
  #
  # Everything stays in floats and is rounded only at the point of display. Rounding each
  # module first and summing afterwards makes the breakdown disagree with the total by a
  # couple of euros, which is exactly the kind of thing an audience spots three rows in.
  class Pricing
    attr_reader :hours, :rate_eur_per_hour, :contingency_pct

    # ``hours`` is the authoritative total the service computed, not a sum of the modules:
    # those are display detail and can carry their own rounding.
    def self.for(run)
      new(hours: run.total_engineer_hours,
          rate_eur_per_hour: run.rate_eur_per_hour,
          contingency_pct: run.contingency_pct)
    end

    def initialize(hours:, rate_eur_per_hour:, contingency_pct:)
      @hours = hours.to_f
      @rate_eur_per_hour = rate_eur_per_hour.to_i
      @contingency_pct = contingency_pct.to_i
    end

    # A rate of zero means "this run is not priced" — the screens hide the money instead of
    # showing a confident 0 €.
    def priced? = rate_eur_per_hour.positive?

    def base = hours * rate_eur_per_hour

    def contingency = base * contingency_pct / 100.0

    def total = base + contingency

    def contingency? = contingency_pct.positive?

    # What travels to the AI service so the proposal can QUOTE the price without deriving
    # it. Rounded here because prose has no use for cents, and because the round trip is
    # then comparable: whatever the agent echoes back must equal exactly this.
    def to_payload
      {
        "currency" => "EUR",
        "rate_eur_per_hour" => rate_eur_per_hour,
        "contingency_pct" => contingency_pct,
        "base_eur" => base.round,
        "contingency_eur" => contingency.round,
        "total_eur" => total.round,
        # Pre-formatted too, so the prose quotes the very same string the tables print.
        # Prose saying "85,301 EUR" right above a table saying "85.301 €" reads like two
        # different numbers.
        "rate_display" => "#{rate_eur_per_hour} €/h",
        "base_display" => display(base),
        "contingency_display" => display(contingency),
        "total_display" => display(total)
      }
    end

    private

    def display(amount)
      "#{amount.round.to_s.reverse.scan(/\d{1,3}/).join('.').reverse} €"
    end
  end
end
