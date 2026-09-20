// Agent color palette for timeline labels
// Maps agent_name → Tailwind color classes (bg + text + border)

const AGENT_COLORS = [
  { bg: 'bg-violet-500/20', text: 'text-violet-300', border: 'border-violet-500/30', dot: 'bg-violet-400' },
  { bg: 'bg-blue-500/20',   text: 'text-blue-300',   border: 'border-blue-500/30',   dot: 'bg-blue-400' },
  { bg: 'bg-cyan-500/20',   text: 'text-cyan-300',   border: 'border-cyan-500/30',   dot: 'bg-cyan-400' },
  { bg: 'bg-green-500/20',  text: 'text-green-300',  border: 'border-green-500/30',  dot: 'bg-green-400' },
  { bg: 'bg-yellow-500/20', text: 'text-yellow-300', border: 'border-yellow-500/30', dot: 'bg-yellow-400' },
  { bg: 'bg-orange-500/20', text: 'text-orange-300', border: 'border-orange-500/30', dot: 'bg-orange-400' },
  { bg: 'bg-pink-500/20',   text: 'text-pink-300',   border: 'border-pink-500/30',   dot: 'bg-pink-400' },
]

// Coordinator always gets violet (index 0)
const COORDINATOR_NAMES = ['coordinator', 'qacoordinator', 'coordinatoragent']

const _cache = new Map<string, typeof AGENT_COLORS[0]>()
let _counter = 1  // Start from 1; 0 reserved for coordinator

export function getAgentColor(agentName: string) {
  if (_cache.has(agentName)) return _cache.get(agentName)!

  const normalized = agentName.toLowerCase().replace(/[^a-z0-9]/g, '')
  if (COORDINATOR_NAMES.some(n => normalized.includes(n))) {
    _cache.set(agentName, AGENT_COLORS[0])
    return AGENT_COLORS[0]
  }

  const color = AGENT_COLORS[_counter % AGENT_COLORS.length]
  _counter++
  _cache.set(agentName, color)
  return color
}

export function getAgentShortName(agentName: string): string {
  // "QAAutomationAgent" → "QAAutomation"
  // "ExploreAgent" → "Explore"
  return agentName
    .replace(/Agent$/, '')
    .replace(/([A-Z])/g, ' $1')
    .trim()
    .replace(/\s+/g, ' ')
    || agentName
}
