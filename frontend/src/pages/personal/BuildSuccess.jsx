/**
 * pages/personal/BuildSuccess.jsx — Iter 212m-235 — Phase 6
 *
 * Celebration screen. Reads the materialize result from
 * sessionStorage (stashed by ShipProgress), fires confetti, shows
 * the live URL + View Code link.
 */
import React, { useEffect, useState } from "react";
import { useNavigate, useParams, Link } from "react-router-dom";
import { motion } from "framer-motion";
import confetti from "canvas-confetti";
import { ExternalLink, Github, Database as DbIcon, ArrowRight } from "lucide-react";
import { PersonalShell, PrimaryButton, SecondaryButton } from "./_shell";

export default function BuildSuccess() {
  const { draftId } = useParams();
  const nav = useNavigate();
  const [result, setResult] = useState(null);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(`aurem_ship_${draftId}`);
      if (raw) setResult(JSON.parse(raw));
    } catch { /* ignore */ }
    // Fire confetti once on mount.
    const timer = setTimeout(() => {
      confetti({
        particleCount: 90, spread: 80, origin: { y: 0.35 },
        colors: ["#E07A5F", "#81B29A", "#F2CC8F", "#FDFDF9"],
        scalar: 0.9,
      });
    }, 200);
    return () => clearTimeout(timer);
  }, [draftId]);

  const liveUrl   = result?.deploy?.live_url;
  const repoUrl   = result?.repo?.html_url;
  const projectId = result?.project_id;

  return (
    <PersonalShell>
      <div
        data-testid="build-success-page"
        style={{
          maxWidth: 720, margin: "0 auto",
          padding: "72px 24px 96px", textAlign: "center",
        }}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }} animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.5, type: "spring", stiffness: 120 }}
        >
          <h1 style={{
            fontFamily: "'Cabinet Grotesk', 'Manrope', sans-serif",
            fontSize: 44, fontWeight: 500, letterSpacing: "-0.03em",
            margin: "0 0 12px",
          }}>
            Your app is live!
          </h1>
          <p style={{ fontSize: 17, color: "#6B6B63", margin: "0 0 44px" }}>
            Congrats — you just built and shipped a working app.
          </p>
        </motion.div>

        {/* Live URL card */}
        <motion.div
          initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.2 }}
          style={{
            background: "rgba(255,255,255,0.7)",
            backdropFilter: "blur(20px)",
            border: "1px solid #E5E5DF",
            borderRadius: 20,
            padding: "32px 28px",
            boxShadow: "0 16px 48px rgba(224,122,95,0.10)",
          }}
        >
          <p style={{
            fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase",
            color: "#8B8B7D", margin: "0 0 8px",
          }}>Live at</p>
          {liveUrl ? (
            <a
              data-testid="success-live-url-link"
              href={liveUrl}
              target="_blank" rel="noreferrer noopener"
              style={{
                fontFamily: "ui-monospace, monospace",
                fontSize: 22, fontWeight: 500,
                color: "#E07A5F", textDecoration: "none",
                display: "inline-flex", alignItems: "center", gap: 10,
                wordBreak: "break-all",
              }}
            >
              {liveUrl.replace(/^https?:\/\//, "")}
              <ExternalLink size={18} />
            </a>
          ) : (
            <p data-testid="success-deploy-pending" style={{
              fontFamily: "ui-monospace, monospace", fontSize: 15,
              color: "#8B8B7D", margin: 0,
            }}>
              Deploy is still processing — check <Link to="/projects" style={{ color: "#E07A5F" }}>Projects</Link> in a minute.
            </p>
          )}
        </motion.div>

        {/* Secondary action buttons */}
        <div style={{
          marginTop: 28, display: "flex", flexWrap: "wrap",
          gap: 12, justifyContent: "center",
        }}>
          {repoUrl && (
            <a
              data-testid="success-view-code-link"
              href={repoUrl} target="_blank" rel="noreferrer noopener"
              style={{ textDecoration: "none" }}
            >
              <SecondaryButton>
                <Github size={15} /> View code
              </SecondaryButton>
            </a>
          )}
          {projectId && (
            <Link
              data-testid="success-view-database-link"
              to={`/projects/${projectId}`}
              style={{ textDecoration: "none" }}
            >
              <SecondaryButton>
                <DbIcon size={15} /> Manage database
              </SecondaryButton>
            </Link>
          )}
        </div>

        <div style={{ marginTop: 48 }}>
          <PrimaryButton
            data-testid="success-build-another-button"
            onClick={() => nav("/build")}
            style={{ padding: "14px 26px" }}
          >
            Build another <ArrowRight size={16} />
          </PrimaryButton>
        </div>

        <p style={{
          marginTop: 40, fontSize: 12, color: "#8B8B7D",
        }}>
          You can revisit or share this app anytime from your{" "}
          <Link to="/projects" style={{ color: "#E07A5F" }}>Projects</Link> page.
        </p>
      </div>
    </PersonalShell>
  );
}
