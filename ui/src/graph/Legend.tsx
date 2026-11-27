import { FORMATS } from "../api/types";
import { FORMAT_COLOR, UNREACHABLE_COLOR } from "../theme";

/** The graph's key: format colours, the ingested-source ring, unreachable versions, and which way
 * arrows point. */
export function Legend() {
  return (
    <div className="legend-card">
      {FORMATS.map((f) => (
        <span key={f} className="legend-row">
          <svg width="14" height="14" aria-hidden>
            <circle cx="7" cy="7" r="4.5" fill={FORMAT_COLOR[f]} />
          </svg>
          {f}
        </span>
      ))}
      <span className="legend-row">
        <svg width="14" height="14" aria-hidden>
          <circle cx="7" cy="7" r="3.5" fill="#9aa1ad" />
          <circle cx="7" cy="7" r="6" fill="none" stroke="#9aa1ad" strokeWidth="1" />
        </svg>
        ingested source
      </span>
      <span className="legend-row">
        <svg width="14" height="14" aria-hidden>
          <circle cx="7" cy="7" r="4.5" fill="none" stroke={UNREACHABLE_COLOR} strokeWidth="1.2" strokeDasharray="2.5 2" />
        </svg>
        unreachable
      </span>
      <span className="legend-row">
        <svg width="22" height="14" aria-hidden>
          <line x1="1" y1="7" x2="15" y2="7" stroke="#8a919d" strokeWidth="1.2" />
          <path d="M21 7 L13 3.6 L15.2 7 L13 10.4 Z" fill="#6f7682" />
        </svg>
        points to the cited document
      </span>
    </div>
  );
}
