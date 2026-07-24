import { memo, useMemo } from "react";
import type { ChapterContent, Token } from "../api/client";
import { RubyText } from "./RubyText";

interface Props {
  content: ChapterContent;
  pinyin: boolean;
  onWord: (word: string, el: HTMLElement) => void;
}

// Backend blocks are normally smaller than this. If an old/malformed API ever
// returns a whole book as one chapter, collapse it to plain paragraph nodes
// instead of creating ruby/word elements for every character.
const MAX_RICH_CHAPTER_CHARS = 20_000;

// Rebuild paragraphs from the flat token stream: "\n" tokens are paragraph
// boundaries. Blank runs collapse so spacing is governed by CSS, not the source.
export function splitParagraphs(tokens: Token[]): Token[][] {
  const paras: Token[][] = [];
  let current: Token[] = [];
  for (const tok of tokens) {
    if (tok.py === null && tok.t.includes("\n")) {
      if (current.length) {
        paras.push(current);
        current = [];
      }
      continue;
    }
    current.push(tok);
  }
  if (current.length) paras.push(current);
  return paras;
}

function ChapterImpl({ content, pinyin, onWord }: Props) {
  const paragraphs = useMemo(() => splitParagraphs(content.tokens), [content.tokens]);
  const rich = useMemo(
    () => content.tokens.reduce((total, token) => total + token.t.length, 0) <= MAX_RICH_CHAPTER_CHARS,
    [content.tokens],
  );
  const positioned = useMemo(() => {
    let offset = 0;
    return paragraphs.map((tokens) => {
      const start = offset;
      const items = tokens.map((token) => {
        const tokenStart = offset;
        offset += token.t.length;
        return { token, start: tokenStart };
      });
      const end = offset;
      offset += 1; // canonical paragraph separator; independent of source CR/LF style
      return { start, end, items };
    });
  }, [paragraphs]);
  return (
    <article className="chapter" data-idx={content.index}>
      <header className="chapter-head">
        <span className="chapter-ord">{String(content.index + 1).padStart(2, "0")}</span>
        <h2 className="chapter-title">{content.title}</h2>
      </header>
      <div className={`chapter-body${pinyin ? " has-pinyin" : ""}`}>
        {positioned.map((paragraph, pi) => (
          <p
            key={pi}
            data-char-start={paragraph.start}
            data-char-end={paragraph.end}
          >
            {rich ? (
              paragraph.items.map(({ token, start }, ti) => (
                <RubyText
                  key={ti}
                  token={token}
                  startOffset={start}
                  pinyin={pinyin}
                  onWord={onWord}
                />
              ))
            ) : (
              <span
                className="plain"
                data-char-start={paragraph.start}
                data-char-length={paragraph.end - paragraph.start}
              >
                {paragraph.items.map(({ token }) => token.t).join("")}
              </span>
            )}
          </p>
        ))}
      </div>
    </article>
  );
}

export const Chapter = memo(ChapterImpl);
