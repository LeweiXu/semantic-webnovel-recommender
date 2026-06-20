import { memo } from "react";
import type { Token } from "../api/client";

interface Props {
  token: Token;
  pinyin: boolean;
  onWord: (word: string, el: HTMLElement) => void;
}

// One token: a Han word rendered as ruby (char + pinyin per character), or
// plain text. Newlines become paragraph breaks upstream, so here "\n" is inert.
function RubyTextImpl({ token, pinyin, onWord }: Props) {
  const { t, py } = token;
  if (py === null) {
    return <span className="plain">{t}</span>;
  }
  const chars = Array.from(t);
  const readings = py.split(" ");
  const handle = (e: React.MouseEvent<HTMLElement>) => onWord(t, e.currentTarget);

  return (
    <span
      className="word"
      role="button"
      tabIndex={0}
      onClick={handle}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onWord(t, e.currentTarget);
        }
      }}
    >
      {chars.map((ch, i) => (
        <ruby key={i}>
          {ch}
          <rt className={pinyin ? "" : "rt-hidden"}>{readings[i] ?? ""}</rt>
        </ruby>
      ))}
    </span>
  );
}

export const RubyText = memo(RubyTextImpl);
