import { LAYOUTS, type Layout } from "./layout";

const TITLE: Record<Layout, string> = {
  rings: "Sources at the centre; each document one ring outside everything it cites",
  columns: "Sources on the left; each document one column right of everything it cites",
  free: "A force-directed layout",
};

/** Chooses how the graph is laid out. */
export function LayoutSwitch({ layout, onChange }: { layout: Layout; onChange: (l: Layout) => void }) {
  return (
    <div className="layout-switch" role="radiogroup" aria-label="Graph layout">
      {LAYOUTS.map((l) => (
        <button
          key={l}
          type="button"
          role="radio"
          aria-checked={l === layout}
          className={l === layout ? "on" : undefined}
          title={TITLE[l]}
          onClick={() => onChange(l)}
        >
          {l}
        </button>
      ))}
    </div>
  );
}
