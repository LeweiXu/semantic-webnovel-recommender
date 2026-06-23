import { useEffect, useMemo, useState } from "react";
import {
  api,
  type MapData,
  type ReadingItem,
  type RecItem,
  type TagCount,
} from "../api/client";
import { useReader } from "../store/reader";
import { SemanticMap } from "./SemanticMap";
import { RecCard } from "./RecCard";

type Action =
  | { kind: "idle" }
  | { kind: "query"; text: string }
  | { kind: "similar"; nid: string; title: string };

const CAT_LABEL: Record<string, string> = { gl: "百合", yanqing: "言情" };
const catLabel = (c: string) => CAT_LABEL[c] ?? c;

export function DiscoverPage() {
  const openNovel = useReader((s) => s.openNovel);

  const [input, setInput] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [action, setAction] = useState<Action>({ kind: "idle" });
  const [results, setResults] = useState<RecItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [slow, setSlow] = useState(false); // first query loads the model
  const [error, setError] = useState<string | null>(null);

  const [map, setMap] = useState<MapData | null>(null);
  const [tags, setTags] = useState<TagCount[]>([]);
  const [reading, setReading] = useState<ReadingItem[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    api.discoverMap().then(setMap).catch(() => {});
    api.discoverTags(16).then(setTags).catch(() => {});
    api.reading().then(setReading).catch(() => {});
  }, []);

  const runQuery = (text: string, cat = category) => {
    const q = text.trim();
    if (!q) return;
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

  const explore = (nid: string, title: string, cat = category) => {
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

  // Re-run the active action when the category filter changes.
  const onCategory = (cat: string | null) => {
    setCategory(cat);
    if (action.kind === "query") runQuery(action.text, cat);
    else if (action.kind === "similar") explore(action.nid, action.title, cat);
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

        {tags.length > 0 && (
          <div className="dsc-tags">
            {tags.map((t) => (
              <button key={t.tag} className="dsc-tagchip" onClick={() => { setInput(t.tag); runQuery(t.tag); }}>
                {t.tag}
              </button>
            ))}
          </div>
        )}
      </header>

      {reading.length > 0 && action.kind === "idle" && (
        <section className="dsc-shelf">
          <div className="dsc-section-label">Continue reading</div>
          <div className="dsc-shelf-row">
            {reading.slice(0, 8).map((r) => (
              <button key={r.nid} className="shelf-card" onClick={() => openNovel(r.nid, r.position)}>
                <span className="shelf-title">{r.title}</span>
                <span className="shelf-meta">
                  ch {r.position + 1}{r.total ? `/${r.total}` : ""}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

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
          {map ? (
            <SemanticMap points={map.points} selected={selected} onSelect={onMapSelect} />
          ) : (
            <div className="dsc-note">Loading map…</div>
          )}
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
