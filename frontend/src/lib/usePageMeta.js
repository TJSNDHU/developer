/**
 * usePageMeta — tiny hook to set <title> + meta description per route.
 * Avoids pulling in react-helmet-async just for two auth pages.
 *
 * Restores the default landing meta on unmount so users navigating back
 * to / don't see stale tab titles.
 */
import { useEffect } from "react";

const DEFAULT_TITLE = "AUREM Dev — Your Autonomous CTO. Ships code to GitHub in minutes.";
const DEFAULT_DESC =
  "AUREM Dev is an autonomous AI engineer that plans, writes, tests and ships features to your GitHub repo. 1,000 tokens free, no card required.";

function setMeta(name, content, attr = "name") {
  let tag = document.querySelector(`meta[${attr}="${name}"]`);
  if (!tag) {
    tag = document.createElement("meta");
    tag.setAttribute(attr, name);
    document.head.appendChild(tag);
  }
  tag.setAttribute("content", content);
}

function setCanonical(href) {
  let tag = document.querySelector('link[rel="canonical"]');
  if (!tag) {
    tag = document.createElement("link");
    tag.setAttribute("rel", "canonical");
    document.head.appendChild(tag);
  }
  tag.setAttribute("href", href);
}

export default function usePageMeta({ title, description, canonical }) {
  useEffect(() => {
    if (title) document.title = title;
    if (description) {
      setMeta("description", description);
      setMeta("og:title", title || DEFAULT_TITLE, "property");
      setMeta("og:description", description, "property");
      setMeta("twitter:title", title || DEFAULT_TITLE);
      setMeta("twitter:description", description);
    }
    if (canonical) setCanonical(canonical);
    return () => {
      document.title = DEFAULT_TITLE;
      setMeta("description", DEFAULT_DESC);
      setMeta("og:title", DEFAULT_TITLE, "property");
      setMeta("og:description", DEFAULT_DESC, "property");
      setMeta("twitter:title", DEFAULT_TITLE);
      setMeta("twitter:description", DEFAULT_DESC);
      setCanonical(window.location.origin + "/");
    };
  }, [title, description, canonical]);
}
