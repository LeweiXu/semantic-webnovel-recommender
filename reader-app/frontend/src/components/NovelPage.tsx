import { useEffect, useState } from "react";
import { api, type NovelDetail } from "../api/client";
import { useReader } from "../store/reader";
import { currentRoute } from "../routing";
import { PagedChapterList } from "./PagedChapterList";

const CAT_LABEL: Record<string, string> = { gl: "百合", yanqing: "言情" };
const catLabel = (c: string) => CAT_LABEL[c] ?? c;

// The collapsed synopsis shows at most this many characters, then "View full".
const SYNOPSIS_CAP = 250;

// A novel's landing page, in the shape of a 52shuku novel page: title, tags,
// synopsis, and the full chapter list. This is a statically-designed page (its
// own fixed typography, matching the Discover/Library pages) — it deliberately
// does NOT follow the reader's per-user font/pinyin settings.
export function NovelPage() {
  const route = currentRoute();
  const id = route.page === "novel" ? route.id : "";
  const openNovel = useReader((s) => s.openNovel);

  const [novel, setNovel] = useState<NovelDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [synopsisOpen, setSynopsisOpen] = useState(false);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    setNovel(null);
    setError(null);
    api
      .novel(id)
      .then((n) => alive && setNovel(n))
      .catch((e) => alive && setError(e?.message ?? "Could not open novel"));
    return () => {
      alive = false;
    };
  }, [id]);

  if (error) return <div className="stage-note">{error}</div>;
  if (!novel) return <div className="stage-note">Opening…</div>;

  const resumeLabel = novel.position > 0 ? `Continue · ch ${novel.position + 1}` : "Start reading";
  const long = novel.synopsis.length > SYNOPSIS_CAP;
  const synopsisText = long && !synopsisOpen
    ? `${novel.synopsis.slice(0, SYNOPSIS_CAP)}…`
    : novel.synopsis;

  return (
    <div className="scroll-root">
      <div className="novel-page">
        <header className="novel-page-hero">
          <h1 className="novel-page-title">{novel.title}</h1>
          <p className="novel-page-byline">
            {novel.author || "—"}
            {novel.category ? ` · ${catLabel(novel.category)}` : ""}
            {novel.total ? ` · ${novel.total} chapters` : ""}
          </p>
          {novel.tags.length > 0 && (
            <div className="novel-page-tags">
              {novel.tags.map((t) => (
                <span key={t} className="lib-tag">{t}</span>
              ))}
            </div>
          )}
          <button className="btn-seal novel-page-read" onClick={() => openNovel(novel.slug)}>
            {resumeLabel}
          </button>
        </header>

        {novel.synopsis && (
          <section className="novel-page-section">
            <div className="dsc-section-label">Synopsis</div>
            <p className="novel-synopsis">{synopsisText}</p>
            {long && (
              <button className="synopsis-toggle" onClick={() => setSynopsisOpen((v) => !v)}>
                {synopsisOpen ? "Show less" : "View full"}
              </button>
            )}
          </section>
        )}

        <section className="novel-page-section">
          <div className="dsc-section-label">Chapters</div>
          <PagedChapterList
            chapters={novel.chapters}
            current={novel.position}
            className="novel-page-toc"
            onSelect={(chapter) => openNovel(novel.slug, { chapter, line: null })}
          />
        </section>
      </div>
    </div>
  );
}
