// Group a plan into reviewable audiences: one per lifecycle stage and action.
export function audienceGroups(rows, actions, filter = () => true) {
  const map = new Map();
  for (const r of rows) {
    if (r.holdout || r.action === 'none' || !filter(r)) continue;
    const key = `${r.c.stage}|${r.action}`;
    const g = map.get(key) || { key, stage: r.c.stage, actionId: r.action, count: 0, spend: 0, net: 0, excluded: 0 };
    g.count++;
    g.spend += r.ev.spend;
    g.net += r.ev.net + (r.retargetSaving || 0);
    if (r.excluded) g.excluded++;
    map.set(key, g);
  }
  return [...map.values()]
    .map((g) => {
      const a = actions.find((x) => x.id === g.actionId);
      return { ...g, actionName: a?.name || g.actionId, label: `${g.stage} · ${a?.name || g.actionId}` };
    })
    .sort((a, b) => b.net - a.net);
}

export function pushedThisCycle(pushLog, key) {
  const cutoff = Date.now() - 30 * 24 * 3600 * 1000;
  return pushLog.some((p) => p.key === key && Date.parse(p.at) > cutoff);
}
