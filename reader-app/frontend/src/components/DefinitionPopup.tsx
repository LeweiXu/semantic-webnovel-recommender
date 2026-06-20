import { useEffect, useRef, useState } from "react";
import { api, type DefineOut } from "../api/client";

export interface PopupTarget {
  word: string;
  rect: DOMRect;
}

interface Props {
  target: PopupTarget | null;
  onClose: () => void;
}

// A small dictionary card pinned under the tapped word. Positioned manually
// (no positioning lib) and clamped to the viewport.
export function DefinitionPopup({ target, onClose }: Props) {
  const [data, setData] = useState<DefineOut | null>(null);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useEffect(() => {
    if (!target) {
      setData(null);
      return;
    }
    let alive = true;
    setLoading(true);
    api
      .define(target.word)
      .then((d) => alive && setData(d))
      .catch(() => alive && setData(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [target]);

  // Place after render so we know the card's measured size.
  useEffect(() => {
    if (!target || !ref.current) return;
    const card = ref.current.getBoundingClientRect();
    const margin = 12;
    let left = target.rect.left + target.rect.width / 2 - card.width / 2;
    left = Math.max(margin, Math.min(left, window.innerWidth - card.width - margin));
    let top = target.rect.bottom + 8;
    if (top + card.height > window.innerHeight - margin) {
      top = target.rect.top - card.height - 8; // flip above
    }
    setPos({ left, top });
  }, [target, data, loading]);

  useEffect(() => {
    if (!target) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onDown);
    };
  }, [target, onClose]);

  if (!target) return null;
  const empty = data && data.entries.length === 0 && data.perChar.length === 0;

  return (
    <div
      ref={ref}
      className="defpopup"
      style={pos ? { left: pos.left, top: pos.top } : { left: -9999, top: -9999 }}
      role="dialog"
    >
      <div className="defpopup-head">
        <span className="defpopup-word">{target.word}</span>
        {data && data.entries[0] && (
          <span className="defpopup-py">{data.entries[0].pinyin}</span>
        )}
      </div>
      {loading && <p className="defpopup-note">Looking up…</p>}
      {data && data.entries.length > 0 && (
        <ol className="defpopup-senses">
          {data.entries.map((e, i) => (
            <li key={i}>
              {data.entries.length > 1 && <span className="defpopup-py">{e.pinyin} </span>}
              {e.defs.join("; ")}
            </li>
          ))}
        </ol>
      )}
      {data && data.perChar.length > 0 && (
        <div className="defpopup-chars">
          {data.entries.length > 0 && <div className="defpopup-sub">By character</div>}
          {data.perChar.map((c, i) => (
            <div className="defpopup-char" key={i}>
              <span className="defpopup-char-h">{c.char}</span>
              <span className="defpopup-py">{c.pinyin}</span>
              <span className="defpopup-char-d">{c.defs.slice(0, 2).join("; ")}</span>
            </div>
          ))}
        </div>
      )}
      {empty && <p className="defpopup-note">No dictionary entry.</p>}
    </div>
  );
}
