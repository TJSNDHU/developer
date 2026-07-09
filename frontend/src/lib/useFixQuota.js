/**
 * lib/useFixQuota.js — Iter 212m-190
 *
 * Task-quota snapshot for the scan-fix surfaces.
 *   GET /api/aurem-dev/fix-pipeline/quota →
 *   { tier, fix_tools: [slug…], bulk_fix, monthly_task_limit,
 *     tasks_used, tasks_remaining (null = unlimited), is_unlimited }
 */
import { useState, useEffect, useCallback } from "react";
import { api } from "./api";

export default function useFixQuota() {
  const [quota, setQuota] = useState(null);
  const refresh = useCallback(() => {
    api.get("/fix-pipeline/quota")
      .then((r) => setQuota(r?.data || null))
      .catch(() => {});
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  return { quota, refresh };
}
