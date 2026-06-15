# AUREM CTO — Deployment Guide

## Current Setup
- Platform: Emergent
- Production: auremcto.com
- Health check: /api/health
- Redeploy: Emergent Dashboard → Manage Deployments → Re-deploy

## Environment Variables (Emergent Secrets)
- REACT_APP_BACKEND_URL = https://auremcto.com
- CORS_ORIGINS = https://auremcto.com,https://www.auremcto.com
- APP_URL = https://auremcto.com

## Blue-Green (Ready when needed)
Scripts in /app/scripts/:
- blue_green_switch.sh — health check before switching
- rollback.sh — emergency rollback guide

## Staging (When ready — no cost until activated)
Config: .emergent/staging.yml
URL: staging.auremcto.com
Cost: Same as prod pod (~$20/month)

## Feature Flags
Admin: auremcto.com/admin (Settings tab)
Feature flags in MongoDB: feature_flags collection

## Cost by Scale
Now:        $0 extra
100 users:  Add staging ($20/month)
1000 users: Blue-Green ($40/month total)
