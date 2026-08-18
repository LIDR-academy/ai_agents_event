# This file should ensure the existence of records required to run the application in every environment (production,
# development, test). The code here should be idempotent so that it can be executed at any point in every environment.
# The data can then be loaded with the bin/rails db:seed command (or created alongside the database with db:setup).
#
# Example:
#
#   ["Action", "Comedy", "Drama", "Horror"].each do |genre_name|
#     MovieGenre.find_or_create_by!(name: genre_name)
#   end

# Session 12 — example agent profiles for the live console. Idempotent: safe to
# re-run. Each is a preset for the hand-written S12 agent (name + knobs + persona).
[
  {
    name: "Estándar",
    is_default: true,
    persona: "Sé claro y conservador; explica cada supuesto en una frase.",
    config: { "model" => "gpt-5", "reasoning_effort" => "medium" }
  },
  {
    name: "Veloz (debug)",
    persona: "Ve al grano; una búsqueda por componente y consolida.",
    config: { "model" => "gpt-5-mini", "reasoning_effort" => "low", "max_iterations" => "6" }
  },
  {
    name: "Exhaustivo",
    persona: "Ante ambigüedad, sobreestima y justifícalo; busca análogos amplios por componente.",
    config: { "model" => "gpt-5", "reasoning_effort" => "high", "max_iterations" => "15",
              "search_top_k" => "8" }
  }
].each do |attrs|
  profile = Agents::Profile.find_or_initialize_by(agent_type: "handwritten", name: attrs[:name])
  profile.persona = attrs[:persona]
  profile.config = attrs[:config]
  profile.is_default = attrs.fetch(:is_default, false)
  profile.save!
end
puts "Seeded #{Agents::Profile.count} agent profile(s)."
