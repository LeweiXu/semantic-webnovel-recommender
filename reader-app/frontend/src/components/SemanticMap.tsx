import { useRef, useState } from "react";
import type { MapPoint } from "../api/client";

interface Props {
  points: MapPoint[];
  selected: string | null;
  onSelect: (nid: string) => void;
}

const CAT_CLASS: Record<string, string> = { gl: "cat-gl", yanqing: "cat-yq" };

// A 2-D PCA projection of the demo corpus' bge-m3 vectors. Each dot is a novel;
// proximity ≈ semantic similarity. Hovering reveals the title; clicking explores
// that novel's neighbourhood. Plain HTML dots (kept perfectly round, easy to
// animate) positioned by percentage from the [-1, 1] PCA coordinates.
export function SemanticMap({ points, selected, onSelect }: Props) {
  const [hover, setHover] = useState<MapPoint | null>(null);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  const wrapRef = useRef<HTMLDivElement>(null);

  const pct = (v: number) => 5 + ((v + 1) / 2) * 90; // [-1,1] → [5%, 95%]

  const track = (e: React.MouseEvent, p: MapPoint) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (rect) setPos({ x: e.clientX - rect.left, y: e.clientY - rect.top });
    setHover(p);
  };

  return (
    <div className="smap" ref={wrapRef}>
      <div className="smap-field">
        {points.map((p, i) => (
          <button
            key={p.nid}
            className={`smap-dot ${CAT_CLASS[p.category] ?? "cat-other"}${
              p.nid === selected ? " is-sel" : ""
            }`}
            style={{
              left: `${pct(p.x)}%`,
              top: `${pct(-p.y)}%`,
              animationDelay: `${Math.min(i, 200) * 4}ms`,
            }}
            onMouseEnter={(e) => track(e, p)}
            onMouseMove={(e) => track(e, p)}
            onMouseLeave={() => setHover(null)}
            onClick={() => onSelect(p.nid)}
            aria-label={p.title}
          />
        ))}
      </div>

      {hover && (
        <div className="smap-tip" style={{ left: pos.x, top: pos.y }}>
          <span className="smap-tip-title">{hover.title}</span>
          {hover.tags.length > 0 && (
            <span className="smap-tip-tags">{hover.tags.join(" · ")}</span>
          )}
        </div>
      )}

      <div className="smap-legend">
        <span className="smap-key cat-gl">百合 · gl</span>
        <span className="smap-key cat-yq">言情 · yanqing</span>
      </div>
    </div>
  );
}
