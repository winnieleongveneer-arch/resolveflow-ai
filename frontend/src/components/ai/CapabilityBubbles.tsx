'use client'

import { motion } from 'framer-motion'
import { cn } from '@/lib/utils'
import { Icons } from '@/components/ui/icons'

interface Capability {
  icon: React.ElementType
  label: string
  query: string
}

const CAPABILITIES: Capability[] = [
  // Only intents the AI Manager can actually answer from stored records.
  // Offering a prompt it would decline makes a principled refusal look like a
  // broken feature, so the suggestions match the supported intents exactly.
  {
    icon: Icons.workbench,
    label: 'Why is a ticket waiting?',
    query: 'Why is ITSM-2211 waiting for a human?',
  },
  {
    icon: Icons.shield,
    label: 'Which policy blocked this?',
    query: 'Which policy prevented this action for ITSM-2199?',
  },
  {
    icon: Icons.alertTriangle,
    label: 'Major-incident candidates',
    query: 'Show current major-incident candidates',
  },
  {
    icon: Icons.clock,
    label: 'Likely SLA breaches',
    query: 'Which tickets are likely to breach?',
  },
  {
    icon: Icons.brain,
    label: 'Active policies',
    query: 'What are the active policies?',
  },
  {
    icon: Icons.barChart,
    label: 'Policy verdict distribution',
    query: 'Show the policy verdict distribution',
  },
]

interface CapabilityBubblesProps {
  onSelect: (query: string) => void
}

export function CapabilityBubbles({ onSelect }: CapabilityBubblesProps) {
  return (
    <div className="flex flex-wrap justify-center gap-2 p-2">
      {CAPABILITIES.map((cap, i) => {
        const Icon = cap.icon
        return (
          <motion.button
            key={i}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.06, duration: 0.25, ease: 'easeOut' }}
            onClick={() => onSelect(cap.query)}
            className={cn(
              'flex items-center gap-2 px-3 py-2 rounded-full',
              'bg-white border border-brand-cornflower/20',
              'text-sm text-brand-navy',
              'shadow-sm',
              'transition-all duration-200',
              'hover:bg-brand-cornflower/10 hover:border-brand-cornflower/40 hover:shadow-md',
              'focus:outline-none focus:ring-2 focus:ring-brand-cornflower/50'
            )}
          >
            <Icon className="h-4 w-4 text-brand-cornflower" strokeWidth={1.5} />
            <span className="text-xs sm:text-sm font-medium">{cap.label}</span>
          </motion.button>
        )
      })}
    </div>
  )
}

