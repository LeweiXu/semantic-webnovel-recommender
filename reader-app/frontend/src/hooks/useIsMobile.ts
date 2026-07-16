import { useEffect, useState } from "react";

// One place decides what counts as "mobile/tablet". The CSS media queries in
// styles/responsive.css use the same 1024px, so keep them in sync if this moves.
export const MOBILE_MAX_WIDTH = 1024;

const QUERY = `(max-width: ${MOBILE_MAX_WIDTH}px)`;

// True on phones and tablets (viewport <= MOBILE_MAX_WIDTH). Re-renders when the
// viewport crosses the threshold (resize, rotate, desktop window drag).
export function useIsMobile(): boolean {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && window.matchMedia(QUERY).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(QUERY);
    const onChange = () => setIsMobile(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);

  return isMobile;
}
