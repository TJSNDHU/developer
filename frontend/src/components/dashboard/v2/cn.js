// Iter 212m-81 — minimal `cn` utility (no clsx dep needed for this scope)
export function cn(...args) {
  return args
    .flat(Infinity)
    .filter((x) => typeof x === "string" && x.length > 0)
    .join(" ");
}
