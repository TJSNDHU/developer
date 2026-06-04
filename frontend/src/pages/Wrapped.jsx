/**
 * Wrapped.jsx — public wrapper around <OraWrapped /> component.
 *
 * Hosts the route `/wrapped` (current user) and `/wrapped/:userId` for
 * sharing — same component, different default audience.
 */
import React from "react";
import Shell, { PageHeader } from "../components/Shell";
import OraWrapped from "../components/OraWrapped";

export default function Wrapped() {
  return (
    <Shell requireAuth>
      <PageHeader
        eyebrow="your year with aurem"
        title="ORA Wrapped"
        sub="Tasks shipped, files touched, brain decisions logged — your build year in one glance."
      />
      <OraWrapped defaultPeriod="this_month" />
    </Shell>
  );
}
