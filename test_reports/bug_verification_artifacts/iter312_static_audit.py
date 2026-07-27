from pathlib import Path
src=Path('/app/frontend/src/components/ChatPanel.jsx').read_text()
# simple facts
facts={
 'timeout_detection': 'ECONNABORTED' in src and 'timeout of \\d+ms exceeded' in src,
 'active_poll': 'getActiveLoop' in src and '/loop/active' in Path('/app/frontend/src/lib/loopApi.js').read_text(),
 'recovery_banner': 'Plan taking longer than expected — still working' in src,
 'failure_card': 'Loop failed to start' in src,
 'open_stream_in_recovery': 'openLoopStream(active.loop_id)' in src[src.find('const isTimeout'):src.find('setMessages((m) => {', src.find('const isTimeout'))],
 'handle_plan_ready_event_sets_loopPlan': 'setLoopPlan(data.plan' in src or 'setLoopPlan(ev.data.plan' in src,
 'showPlanCard_allows_awaiting_confirmation': 'loopPhase === "awaiting_confirmation"' in src[src.find('const showPlanCard'):src.find('return (', src.find('const showPlanCard'))],
 'showPlanCard_requires_plan_pending': 'loopPhase === "plan_pending"' in src[src.find('const showPlanCard'):src.find('return (', src.find('const showPlanCard'))],
}
for k,v in facts.items(): print(k, v)
print('\nRECOVERY_BLOCK:\n', src[src.find('// ── Iter 312'):src.find('setMessages((m) => {', src.find('// ── Iter 312'))])
print('\nSHOW_PLAN_CARD_BLOCK:\n', src[src.find('const showPlanCard'):src.find('return (', src.find('const showPlanCard'))])
