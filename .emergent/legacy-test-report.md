# Legacy Test Report — deferred failures (founder ruling 2026-07-29)

Deliberate deferral: these iter36–iter267-era failures are QUARANTINED
(@pytest.mark.legacy via tests/legacy_quarantine.txt), not fixed, not
deleted. The CI blocking lane excludes them; the non-blocking legacy
lane in ci.yml re-runs and re-reports them on every push.

Generated: 2026-07-29T05:29:01Z (preview)
Quarantined nodeids: 259 (238 FAILED + 21 setup ERRORS)
Files affected: 117

## Per-file breakdown
```
     11 tests/test_aurem_p0_bugs.py
      6 tests/test_iter212m32_onboarding_nudge.py
      6 tests/test_iter212m121_fix_pipeline.py
      5 tests/test_ship_turn_index.py
      5 tests/test_iter267_url_fetch_retry.py
      5 tests/test_iter212m21_ask_advisor_glm.py
      5 tests/test_iter138_execute_bash_tool.py
      5 tests/test_iter130_layered_persona.py
      5 tests/test_iter123g_seo_geo_consistency.py
      5 tests/test_aurem_chat_persistence.py
      4 tests/test_tool_reliability_v2.py
      4 tests/test_iter80_seo_pwa.py
      4 tests/test_iter36_anti_hallucination.py
      4 tests/test_iter212m3_activation_funnel.py
      4 tests/test_iter212m212_advisor_screen_share.py
      4 tests/test_iter212m17_topup_alerts.py
      4 tests/test_iter212m114_real_finding_fix.py
      4 tests/test_iter212m110_founder_bypass_and_graph.py
      4 tests/test_iter124h_vs_devin_page.py
      4 tests/test_aurem_backend.py
      3 tests/test_iter94_maxx_cap_and_usd_migration.py
      3 tests/test_iter86_architecture_health.py
      3 tests/test_iter79_web_skills.py
      3 tests/test_iter76_preview_pane.py
      3 tests/test_iter69_brain_dump_and_build_hash.py
      3 tests/test_iter37_404_hallucination_guard.py
      3 tests/test_iter341_predeploy.py
      3 tests/test_iter212m6_tool_reliability_full.py
      3 tests/test_iter212m66_vanguard_two_round.py
      3 tests/test_iter212m237_security_gate.py
      3 tests/test_iter212m215_mermaid_diagram.py
      3 tests/test_iter212m159_parliament_v2_routing.py
      3 tests/test_iter212m106_real_ship_and_sanitizer.py
      3 tests/test_iter205_pat_decryption_in_tools.py
      3 tests/test_iter118_route_cache.py
      3 tests/test_iter113_oauth_login_cancel.py
      3 tests/test_iter103_identity_no_fabrication.py
      2 tests/test_token_enforcement.py
      2 tests/test_llm_provider.py
      2 tests/test_iter89_ship_button_no_reappear.py
      2 tests/test_iter83_handoff_guard.py
      2 tests/test_iter82_oauth_signup.py
      2 tests/test_iter77_overview_arch.py
      2 tests/test_iter73_ops_recipes.py
      2 tests/test_iter73_live_tape.py
      2 tests/test_iter66_design_tokens_lock.py
      2 tests/test_iter64_responsive_sweep.py
      2 tests/test_iter55_tool_call_leak_and_timeout.py
      2 tests/test_iter54_shipwall_wrapped_overview.py
      2 tests/test_iter52_production_bug_fixes.py
      2 tests/test_iter212m27_vanguard_hardening.py
      2 tests/test_iter212m232_phase2_github_boilerplate.py
      2 tests/test_iter212m231_phase1_blank_slate.py
      2 tests/test_iter212m22_ask_advisor_full_response.py
      2 tests/test_iter212m211_advisor_tool_leak.py
      2 tests/test_iter212m15_warmstart_timeout_and_monaco_overlay.py
      2 tests/test_iter212m152_prompt_mode_gaps.py
      2 tests/test_iter212m127_log_noise_fixes.py
      2 tests/test_iter212m111_night_mode_focus_manual_ship.py
      2 tests/test_iter212l_persona_and_tool_bridge_hardening.py
      2 tests/test_iter212g_orchestrator_local_ctx_and_openrouter_model.py
      2 tests/test_iter169_fix_hallucination_guards.py
      2 tests/test_iter165_warm_start.py
      2 tests/test_iter165_smart_router_agents.py
      2 tests/test_iter124_repo_first_and_retry.py
      2 tests/test_iter101_annual_referral_overage.py
      2 tests/test_aurem_rollback.py
      1 tests/test_subscription_tiers.py
      1 tests/test_regression_iter289_track1_lane_a.py
      1 tests/test_regression_iter287_qa_matrix_and_mcp_tools.py
      1 tests/test_jwt_revocation.py
      1 tests/test_iter97_vercel_api_token.py
      1 tests/test_iter96_sentry_live.py
      1 tests/test_iter88_admin_and_wall.py
      1 tests/test_iter86_fixes.py
      1 tests/test_iter81_mode_b_council.py
      1 tests/test_iter78_code_surface.py
      1 tests/test_iter78_automations_ui.py
      1 tests/test_iter76_routing.py
      1 tests/test_iter75_gap_coverage.py
      1 tests/test_iter74_gaps.py
      1 tests/test_iter72_vscode_extension_artifact.py
      1 tests/test_iter58_truncated_tree_rescue.py
      1 tests/test_iter44_vanguard.py
      1 tests/test_iter22_live_founder_bypass.py
      1 tests/test_iter212m_prod_e2e_founder.py
      1 tests/test_iter212m6_wiring_audit.py
      1 tests/test_iter212m53_advisor_dedicated_config.py
      1 tests/test_iter212m34_card_footer_and_homepage_pill.py
      1 tests/test_iter212m30_pr2_founder_indexing.py
      1 tests/test_iter212m28_repo_context_parallel.py
      1 tests/test_iter212m234_phase5_sweeper.py
      1 tests/test_iter212m233_phase3_4_deploy_managed_db.py
      1 tests/test_iter212m225_boundary_hardening.py
      1 tests/test_iter212m20_admin_2fa_and_tabbar.py
      1 tests/test_iter212m177_prod_reliability.py
      1 tests/test_iter212m175_mcp_scoped.py
      1 tests/test_iter212m172_five_fixes_and_timeout.py
      1 tests/test_iter212m160_pre_launch_p0.py
      1 tests/test_iter212m158_admin_gate_and_tools_page.py
      1 tests/test_iter212m153_observability_systemstats_refactor.py
      1 tests/test_iter212m150_parliament.py
      1 tests/test_iter212m149_intent_gateway.py
      1 tests/test_iter212m116_repo_map_and_file_selector.py
      1 tests/test_iter212m103_composer_redesign.py
      1 tests/test_iter212k_force_tool_calls_and_truncation_layers.py
      1 tests/test_iter165_codebase_graph.py
      1 tests/test_iter165_brain_v2.py
      1 tests/test_iter162_all_modes_audit.py
      1 tests/test_iter129_chat_latency_budget.py
      1 tests/test_iter124g_persona_quality_score.py
      1 tests/test_iter124f_project_scoping_isolation.py
      1 tests/test_iter123f_external_services_registry.py
      1 tests/test_iter109_oauth_cancel.py
      1 tests/test_iter107_ora_circuit_breaker.py
      1 tests/test_iter101_2_frontend_referral_annual_ui.py
      1 tests/test_integration_health_cron.py
```

## Full nodeid list
See backend/tests/legacy_quarantine.txt (single source of truth).
