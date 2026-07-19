import { memo, useMemo } from "react";
import type { ChapterContent, Token } from "../api/client";
import { RubyText } from "./RubyText";

interface Props {
  content: ChapterContent;
  pinyin: boolean;
  onWord: (word: string, el: HTMLElement) => void;
}

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
            {paragraph.items.map(({ token, start }, ti) => (
              <RubyText
                key={ti}
                token={token}
                startOffset={start}
                pinyin={pinyin}
                onWord={onWord}
              />
            ))}
          </p>
        ))}
      </div>
    </article>
  );
}

export const Chapter = memo(ChapterImpl);
