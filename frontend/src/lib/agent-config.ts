export type AgentConfig = {
  displayName: string
  colorClass: string    // Tailwind text-* class
  bgClass: string       // Tailwind bg-*/10 class
  borderClass: string   // Tailwind border-*/30 class
  dotClass: string      // Tailwind bg-* for the status dot
}

const CONFIGS: Record<string, AgentConfig> = {
  TeamLeadAgent: {
    displayName: 'Dealight Lead',
    colorClass: 'text-emerald-400',
    bgClass: 'bg-emerald-400/10',
    borderClass: 'border-emerald-400/30',
    dotClass: 'bg-emerald-400',
  },
  SalesAgent: {
    displayName: 'Sales Analyst',
    colorClass: 'text-amber-400',
    bgClass: 'bg-amber-400/10',
    borderClass: 'border-amber-400/30',
    dotClass: 'bg-amber-400',
  },
  ForecastAgent: {
    displayName: 'Forecast Analyst',
    colorClass: 'text-sky-400',
    bgClass: 'bg-sky-400/10',
    borderClass: 'border-sky-400/30',
    dotClass: 'bg-sky-400',
  },
}

const DEFAULT: AgentConfig = {
  displayName: 'Agent',
  colorClass: 'text-zinc-400',
  bgClass: 'bg-zinc-400/10',
  borderClass: 'border-zinc-400/30',
  dotClass: 'bg-zinc-400',
}

export function getAgentConfig(agent: string): AgentConfig {
  return CONFIGS[agent] ?? { ...DEFAULT, displayName: agent }
}
