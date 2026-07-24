import { memo } from "react";
import type { Token } from "../api/client";

interface Props {
  token: Token;
  pinyin: boolean;
  onWord: (word: string, el: HTMLElement) => void;
  startOffset?: number;
}

// One token: a Han word rendered as ruby (char + pinyin per character), or
// plain text. Newlines become paragraph breaks upstream, so here "\n" is inert.
function RubyTextImpl({ token, pinyin, onWord, startOffset }: Props) {
  const { t, py } = token;
  if (py === null) {
    return (
      <span
        className="plain"
        data-char-start={startOffset}
        data-char-length={startOffset === undefined ? undefined : t.length}
      >
        {t}
      </span>
    );
  }
  const chars = Array.from(t);
  const readings = py.split(" ");
  let consumed = 0;
  const handle = (e: React.MouseEvent<HTMLElement>) => {
    // Drag-selecting a chapter heading should open the chapter-pattern action,
    // not a dictionary popup for the word under the pointer.
    if (!window.getSelection()?.isCollapsed) return;
    onWord(t, e.currentTarget);
  };

  return (
    <span
      className="word"
      role="button"
      tabIndex={0}
      data-char-start={startOffset}
      data-char-length={startOffset === undefined ? undefined : t.length}
      onClick={handle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onWord(t, e.currentTarget);
        }
      }}
    >
      {chars.map((ch, i) => {
        const offset = startOffset === undefined ? undefined : startOffset + consumed;
        consumed += ch.length;
        return (
          <ruby key={i} data-char-offset={offset}>
            {ch}
            <rt className={pinyin ? "" : "rt-hidden"}>{readings[i] ?? ""}</rt>
          </ruby>
        );
      })}
    </span>
  );
}

export const RubyText = memo(RubyTextImpl);
