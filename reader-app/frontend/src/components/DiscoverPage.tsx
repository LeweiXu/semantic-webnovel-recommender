import { useEffect, useMemo, useState } from "react";
import { api, type MapData, type RecItem } from "../api/client";
import { SemanticMap } from "./SemanticMap";
import { RecCard } from "./RecCard";
import { currentRoute, discoverPath, writeUrl } from "../routing";

type Action =
  | { kind: "idle" }
  | { kind: "query"; text: string }
  | { kind: "similar"; nid: string; title: string };

const CAT_LABEL: Record<string, string> = { gl: "百合", yanqing: "言情" };
const catLabel = (c: string) => CAT_LABEL[c] ?? c;

// Suggested-search chips: the top tags of the demo corpus, hardcoded so they
// render instantly instead of waiting on a /discover/tags round-trip.
const DISCOVER_TAGS = [
  "情有独钟", "甜文", "天作之合", "甜宠文", "轻松", "都市", "爽文", "破镜重圆",
  "强强", "日常", "系统", "穿书", "先婚后爱", "年下", "校园", "豪门总裁",
];

export function DiscoverPage() {
  const initialRoute = currentRoute();
  const initial = initialRoute.page === "discover" ? initialRoute : null;
  const [input, setInput] = useState(initial?.q ?? "");
  const [category, setCategory] = useState<string | null>(initial?.category ?? null);
  const [action, setAction] = useState<Action>({ kind: "idle" });
  const [results, setResults] = useState<RecItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [slow, setSlow] = useState(false); // first query loads the model
  const [error, setError] = useState<string | null>(null);

  const [map, setMap] = useState<MapData | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    api.discoverMap().then(setMap).catch(() => {});
  }, []);

  const runQuery = (text: string, cat = category, updateRoute = true) => {
    const q = text.trim();
    if (!q) return;
    if (updateRoute) writeUrl(discoverPath({ q, category: cat }), false);
    setAction({ kind: "query", text: q });
    setSelected(null);
    setLoading(true);
    setError(null);
    const slowTimer = window.setTimeout(() => setSlow(true), 1200);
    api
      .recommend(q, 14, cat ?? undefined)
      .then((r) => {
        if (r.error) {
          setResults([]);
          setError(r.error);
        } else {
          setError(null);
          setResults(r.results);
        }
      })
      .catch(() => setError("Search failed. Is the demo corpus built?"))
      .finally(() => {
        window.clearTimeout(slowTimer);
        setSlow(false);
        setLoading(false);
      });
  };

  const explore = (nid: string, title: string, cat = category, updateRoute = true) => {
    if (updateRoute) writeUrl(discoverPath({ similar: nid, title, category: cat }), false);
    setAction({ kind: "similar", nid, title });
    setSelected(nid);
    setLoading(true);
    setError(null);
    api
      .similar(nid, 14, cat ?? undefined)
      .then((r) => setResults(r.results))
      .catch(() => setError("Could not load similar novels."))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    if (initial?.similar) explore(initial.similar, initial.title || "this novel", initial.category, false);
    else if (initial?.q) runQuery(initial.q, initial.category, false);
    // Route state is captured once; App remounts this page on back/forward.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-run the active action when the category filter changes.
  const onCategory = (cat: string | null) => {
    setCategory(cat);
    if (action.kind === "query") runQuery(action.text, cat);
    else if (action.kind === "similar") explore(action.nid, action.title, cat);
    else writeUrl(discoverPath({ category: cat }), false);
  };

  const onMapSelect = (nid: string) => {
    const p = map?.points.find((pt) => pt.nid === nid);
    explore(nid, p?.title ?? "this novel");
    document.getElementById("rec-results")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const heading = useMemo(() => {
    if (action.kind === "query") return <>Results for <em>“{action.text}”</em></>;
    if (action.kind === "similar") return <>More like <em>《{action.title}》</em></>;
    return null;
  }, [action]);

  return (
    <div className="discover">
      <header className="dsc-hero">
        <span className="seal-glyph small" aria-hidden>觅</span>
        <h1 className="dsc-title">Discover</h1>
        <p className="dsc-sub">
          A demo over {map?.count ?? 500} novels (250 百合 gl + 250 言情 yanqing)
          with precomputed bge-m3 embeddings; the full index scales to 100k+.
          Describe a story in any words, or explore the embedding map below.
        </p>

        <form
          className="dsc-search"
          onSubmit={(e) => {
            e.preventDefault();
            runQuery(input);
          }}
        >
          <input
            className="dsc-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="破镜重圆，刑侦，ABO…  ·  enemies to lovers detective story…"
            spellCheck={false}
            autoComplete="off"
          />
          <button className="dsc-go" type="submit" aria-label="Search">
            Search
          </button>
        </form>

        <div className="dsc-cats">
          {([null, "gl", "yanqing"] as (string | null)[]).map((c) => (
            <button
              key={c ?? "all"}
              className={`dsc-cat${category === c ? " is-on" : ""}`}
              onClick={() => onCategory(c)}
            >
              {c === null ? "All" : `${catLabel(c)} ${c}`}
            </button>
          ))}
        </div>

        <div className="dsc-tags">
          {DISCOVER_TAGS.map((t) => (
            <button key={t} className="dsc-tagchip" onClick={() => { setInput(t); runQuery(t); }}>
              {t}
            </button>
          ))}
        </div>
      </header>

      <div id="rec-results" className="dsc-body">
        <section className="dsc-results">
          {heading && <div className="dsc-results-head">{heading}</div>}
          {loading && (
            <div className="dsc-note">
              Searching…{slow && " (first query loads the bge-m3 model, a few seconds)"}
            </div>
          )}
          {error && !loading && <div className="dsc-note">{error}</div>}
          {!loading && !error && action.kind === "idle" && (
            <div className="dsc-note dsc-idle">
              Search above, tap a tag, or click a point on the map to explore the
              corpus by semantic similarity.
            </div>
          )}
          {!loading && !error && results.length > 0 && (
            <ul className="rec-list">
              {results.map((r, i) => (
                <RecCard
                  key={r.nid}
                  rec={r}
                  delay={Math.min(i, 12) * 35}
                  onSimilar={(nid, title) => explore(nid, title)}
                  onTag={(t) => { setInput(t); runQuery(t); }}
                />
              ))}
            </ul>
          )}
        </section>

        <section className="dsc-map-panel">
          <div className="dsc-section-label">The semantic map</div>
          {/* The map frame renders immediately; points fade in (dot-in) once the
              backend responds, so the background never spawns in late. */}
          <SemanticMap points={map?.points ?? []} selected={selected} onSelect={onMapSelect} />
          <p className="dsc-map-caption">
            Each point is a novel, placed by a 2-D PCA of its bge-m3 embedding.
            Closer points are more semantically alike; click one to explore its
            neighbourhood.
          </p>
        </section>
      </div>
    </div>
  );
}
