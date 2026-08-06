'use client'

/**
 * The Workbench — the human decision queue.
 *
 * Every item arrives with the case context, the policy verdict and its
 * reasons, the proposed action, the verification and rollback plans, and the
 * agent's recommendation. A person approves, modifies or rejects; the
 * decision is recorded and the workflow resumes from there.
 *
 * Nothing here is seeded. An empty queue means the agent has not escalated
 * anything yet, which is the correct thing to show.
 */

import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import toast from 'react-hot-toast'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Icons } from '@/components/ui/icons'
import { cn } from '@/lib/utils'
import { apiClient } from '@/lib/api-client'

type Json = Record<string, unknown>

interface WorkbenchItem {
  id: string
  run_id: string
  issue_key: string
  status: string
  request_type: string
  case_context: Json
  proposed_action: Json
  policy_result: { verdict?: string; policy_key?: string; policy_version?: number; reasons?: string[] } | null
  agent_recommendation: string | null
  verification_plan: Json | null
  rollback_plan: Json | null
  human_decision: string | null
  modified_action: Json | null
  approved_scope: Json | null
  reviewer: string | null
  reviewer_notes: string | null
  notification_ref: string | null
  created_at: string | null
  decided_at: string | null
}

const STATUS_STYLES: Record<string, string> = {
  PENDING: 'bg-amber-100 text-amber-800 ring-amber-600/20',
  APPROVED: 'bg-emerald-100 text-emerald-800 ring-emerald-600/20',
  MODIFIED: 'bg-blue-100 text-blue-800 ring-blue-600/20',
  REJECTED: 'bg-rose-100 text-rose-800 ring-rose-600/20',
  EXPIRED: 'bg-slate-100 text-slate-700 ring-slate-600/20',
}

const VERDICT_STYLES: Record<string, string> = {
  ALLOW: 'bg-emerald-100 text-emerald-800 ring-emerald-600/20',
  REQUIRE_HUMAN_REVIEW: 'bg-amber-100 text-amber-800 ring-amber-600/20',
  DENY: 'bg-rose-100 text-rose-800 ring-rose-600/20',
}

function Pill({ text, styles }: { text: string; styles?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
        styles || 'bg-slate-100 text-slate-700 ring-slate-600/20'
      )}
    >
      {text}
    </span>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className='space-y-1'>
      <p className='text-xs font-semibold uppercase tracking-wide text-muted-foreground'>
        {label}
      </p>
      <div className='text-sm text-foreground'>{children}</div>
    </div>
  )
}

function JsonBlock({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <p className='text-sm italic text-muted-foreground'>Not supplied</p>
  }
  return (
    <pre className='max-h-56 overflow-auto rounded-lg bg-slate-950/95 p-3 font-mono text-xs leading-relaxed text-slate-100'>
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

export default function WorkbenchPage() {
  const [items, setItems] = useState<WorkbenchItem[]>([])
  const [selected, setSelected] = useState<WorkbenchItem | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [showAll, setShowAll] = useState(false)

  const [reviewer, setReviewer] = useState('')
  const [notes, setNotes] = useState('')
  const [modifyOpen, setModifyOpen] = useState(false)
  const [modifiedJson, setModifiedJson] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const query = showAll ? '' : '?status=PENDING'
      const data = await apiClient.get<WorkbenchItem[]>(`/api/workbench${query}`)
      setItems(data)
      setSelected((prev) => (prev ? data.find((i) => i.id === prev.id) || null : null))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load the queue')
    } finally {
      setLoading(false)
    }
  }, [showAll])

  useEffect(() => {
    void load()
  }, [load])

  // Poll while anything is pending, so the queue moves when the agent escalates.
  useEffect(() => {
    const t = setInterval(() => void load(), 10000)
    return () => clearInterval(t)
  }, [load])

  function select(item: WorkbenchItem) {
    setSelected(item)
    setModifyOpen(false)
    setNotes('')
    setModifiedJson(JSON.stringify(item.proposed_action ?? {}, null, 2))
  }

  async function decide(decision: 'APPROVE' | 'MODIFY' | 'REJECT') {
    if (!selected) return
    if (!reviewer.trim()) {
      toast.error('Enter your name — every decision is attributed.')
      return
    }

    let modified_action: Json | undefined
    if (decision === 'MODIFY') {
      try {
        modified_action = JSON.parse(modifiedJson)
      } catch {
        toast.error('The modified action is not valid JSON.')
        return
      }
    }

    setSubmitting(true)
    try {
      const res = await apiClient.post<{ message: string }>(
        `/api/workbench/${selected.id}/decision`,
        { decision, reviewer: reviewer.trim(), reviewer_notes: notes || null, modified_action }
      )
      toast.success(res.message)
      setModifyOpen(false)
      setNotes('')
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to record the decision')
    } finally {
      setSubmitting(false)
    }
  }

  const pendingCount = items.filter((i) => i.status === 'PENDING').length

  return (
    <div className='space-y-6 p-6'>
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        className='flex flex-wrap items-end justify-between gap-4'
      >
        <div>
          <h1 className='text-3xl font-bold tracking-tight text-brand-navy'>Workbench</h1>
          <p className='mt-1 text-sm text-muted-foreground'>
            Decisions the agent must not make alone. Nothing executes until a human decides.
          </p>
        </div>
        <div className='flex items-center gap-2'>
          <Pill
            text={`${pendingCount} pending`}
            styles={pendingCount ? STATUS_STYLES.PENDING : undefined}
          />
          <Button variant='outline' size='sm' onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'Pending only' : 'Show all'}
          </Button>
          <Button variant='outline' size='sm' onClick={() => void load()} disabled={loading}>
            <Icons.refresh className={cn('mr-1.5 h-4 w-4', loading && 'animate-spin')} />
            Refresh
          </Button>
        </div>
      </motion.div>

      <div className='grid grid-cols-12 gap-6'>
        {/* Queue */}
        <div className='col-span-12 space-y-3 lg:col-span-4'>
          {loading && items.length === 0 && (
            <p className='text-sm text-muted-foreground'>Loading queue…</p>
          )}

          {!loading && items.length === 0 && (
            <Card>
              <CardContent className='py-10 text-center'>
                <Icons.checkCircle className='mx-auto h-8 w-8 text-emerald-500' strokeWidth={1.5} />
                <p className='mt-3 text-sm font-medium text-foreground'>Queue is clear</p>
                <p className='mt-1 text-xs text-muted-foreground'>
                  No exceptions are waiting. Items appear here when the policy engine
                  returns REQUIRE_HUMAN_REVIEW.
                </p>
              </CardContent>
            </Card>
          )}

          {items.map((item) => (
            <motion.div key={item.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <Card
                onClick={() => select(item)}
                className={cn(
                  'cursor-pointer transition-all hover:shadow-md',
                  selected?.id === item.id && 'ring-2 ring-brand-cornflower'
                )}
              >
                <CardContent className='space-y-2 p-4'>
                  <div className='flex items-center justify-between gap-2'>
                    <span className='font-mono text-sm font-semibold'>{item.issue_key}</span>
                    <Pill text={item.status} styles={STATUS_STYLES[item.status]} />
                  </div>
                  <p className='text-sm text-foreground'>
                    {String(item.case_context?.summary ?? item.request_type)}
                  </p>
                  <div className='flex flex-wrap items-center gap-2'>
                    <Pill text={item.request_type} />
                    {item.policy_result?.verdict && (
                      <Pill
                        text={item.policy_result.verdict}
                        styles={VERDICT_STYLES[item.policy_result.verdict]}
                      />
                    )}
                  </div>
                  {item.created_at && (
                    <p className='text-xs text-muted-foreground'>
                      Raised {new Date(item.created_at).toLocaleString()}
                    </p>
                  )}
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>

        {/* Detail */}
        <div className='col-span-12 lg:col-span-8'>
          {!selected ? (
            <Card>
              <CardContent className='py-16 text-center text-sm text-muted-foreground'>
                Select an item to review its full context.
              </CardContent>
            </Card>
          ) : (
            <Card>
              <CardHeader>
                <div className='flex flex-wrap items-center justify-between gap-3'>
                  <CardTitle className='flex items-center gap-2'>
                    <Icons.workbench className='h-5 w-5 text-brand-cornflower' strokeWidth={1.5} />
                    {selected.issue_key}
                  </CardTitle>
                  <div className='flex items-center gap-2'>
                    <Pill text={selected.request_type} />
                    <Pill text={selected.status} styles={STATUS_STYLES[selected.status]} />
                  </div>
                </div>
              </CardHeader>

              <CardContent className='space-y-6'>
                {/* Why a human is here */}
                {selected.policy_result && (
                  <div className='rounded-xl border border-amber-200 bg-amber-50/60 p-4'>
                    <div className='flex items-center gap-2'>
                      <Icons.shield className='h-4 w-4 text-amber-700' strokeWidth={1.5} />
                      <p className='text-sm font-semibold text-amber-900'>
                        {selected.policy_result.policy_key} v{selected.policy_result.policy_version}
                        {' — '}
                        {selected.policy_result.verdict}
                      </p>
                    </div>
                    <ul className='mt-2 list-inside list-disc space-y-1 text-sm text-amber-900'>
                      {(selected.policy_result.reasons || []).map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className='grid gap-5 sm:grid-cols-2'>
                  <Field label='Case context'>
                    <JsonBlock value={selected.case_context} />
                  </Field>
                  <Field label='Proposed action'>
                    <JsonBlock value={selected.proposed_action} />
                  </Field>
                  <Field label='Verification plan'>
                    <JsonBlock value={selected.verification_plan} />
                  </Field>
                  <Field label='Rollback plan'>
                    <JsonBlock value={selected.rollback_plan} />
                  </Field>
                </div>

                <Field label='Agent recommendation'>
                  <p className='rounded-lg bg-slate-50 p-3 text-sm'>
                    {selected.agent_recommendation || 'No recommendation supplied.'}
                  </p>
                </Field>

                {selected.notification_ref && (
                  <p className='text-xs text-muted-foreground'>
                    Escalation: <span className='font-mono'>{selected.notification_ref}</span>
                  </p>
                )}

                {/* Decision */}
                {selected.status === 'PENDING' ? (
                  <div className='space-y-4 rounded-xl border border-slate-200 bg-slate-50/60 p-4'>
                    <div className='grid gap-3 sm:grid-cols-2'>
                      <div className='space-y-1'>
                        <label className='text-xs font-semibold uppercase tracking-wide text-muted-foreground'>
                          Reviewer
                        </label>
                        <input
                          value={reviewer}
                          onChange={(e) => setReviewer(e.target.value)}
                          placeholder='Your name'
                          className='w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-brand-cornflower'
                        />
                      </div>
                      <div className='space-y-1'>
                        <label className='text-xs font-semibold uppercase tracking-wide text-muted-foreground'>
                          Notes
                        </label>
                        <input
                          value={notes}
                          onChange={(e) => setNotes(e.target.value)}
                          placeholder='Why you decided this'
                          className='w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-brand-cornflower'
                        />
                      </div>
                    </div>

                    {modifyOpen && (
                      <div className='space-y-1'>
                        <label className='text-xs font-semibold uppercase tracking-wide text-muted-foreground'>
                          Amended action — only this scope will execute
                        </label>
                        <textarea
                          value={modifiedJson}
                          onChange={(e) => setModifiedJson(e.target.value)}
                          rows={10}
                          className='w-full rounded-lg border border-slate-300 bg-slate-950/95 p-3 font-mono text-xs text-slate-100 outline-none focus:ring-2 focus:ring-brand-cornflower'
                        />
                      </div>
                    )}

                    <div className='flex flex-wrap gap-2'>
                      <Button onClick={() => void decide('APPROVE')} disabled={submitting}>
                        <Icons.checkCircle className='mr-1.5 h-4 w-4' /> Approve
                      </Button>
                      {!modifyOpen ? (
                        <Button variant='outline' onClick={() => setModifyOpen(true)} disabled={submitting}>
                          <Icons.pencil className='mr-1.5 h-4 w-4' /> Modify
                        </Button>
                      ) : (
                        <Button variant='outline' onClick={() => void decide('MODIFY')} disabled={submitting}>
                          <Icons.check className='mr-1.5 h-4 w-4' /> Submit amended action
                        </Button>
                      )}
                      <Button
                        variant='outline'
                        className='text-rose-700 hover:bg-rose-50'
                        onClick={() => void decide('REJECT')}
                        disabled={submitting}
                      >
                        <Icons.close className='mr-1.5 h-4 w-4' /> Reject
                      </Button>
                    </div>

                    <p className='text-xs text-muted-foreground'>
                      No timeout approves this item. It stays pending until someone decides.
                    </p>
                  </div>
                ) : (
                  <div className='space-y-3 rounded-xl border border-slate-200 bg-white p-4'>
                    <Field label='Decision'>
                      <p>
                        <span className='font-semibold'>{selected.human_decision}</span>
                        {selected.reviewer ? ` by ${selected.reviewer}` : ''}
                        {selected.decided_at
                          ? ` on ${new Date(selected.decided_at).toLocaleString()}`
                          : ''}
                      </p>
                    </Field>
                    {selected.reviewer_notes && (
                      <Field label='Notes'>
                        <p>{selected.reviewer_notes}</p>
                      </Field>
                    )}
                    {selected.modified_action && (
                      <Field label='Original recommendation (kept as a learning signal)'>
                        <JsonBlock value={selected.proposed_action} />
                      </Field>
                    )}
                    <Field label='Approved scope — what actually executes'>
                      <JsonBlock value={selected.approved_scope} />
                    </Field>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  )
}
