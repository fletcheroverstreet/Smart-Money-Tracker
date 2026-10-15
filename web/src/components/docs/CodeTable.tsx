/**
 * The full Form 4 transaction-code table, exactly as GET /api/meta/codes
 * returns it. The API builds that list from the same dictionary the loader
 * uses (backend smt/domain/codes.py), so this table can't drift from the logic.
 */
import { Direction } from "@/components/ui/Direction";
import { ErrorState } from "@/components/ui/States";
import type { CodeOut } from "@/lib/api/types";

const th = "px-3 py-2 text-left text-[12px] font-medium leading-4 text-dim";
const td = "px-3 py-2 align-top";

/** P and S are the only codes that count as buying or selling (backend: direction_for). */
function Counts({ code, openMarket }: { code: string; openMarket: boolean }) {
  if (!openMarket) return <span className="text-dim">No</span>;
  // Glyph + word + color, never color alone: "▲ Buy" / "▼ Sell". If the API ever marks
  // another code as open-market, Direction falls back to the plain word "Yes".
  return <Direction direction={code === "P" ? "buy" : code === "S" ? "sell" : null} label="Yes" compact />;
}

export function CodeTable({ codes }: { codes: CodeOut[] | null }) {
  if (codes === null) {
    return <ErrorState title="The code table didn't load" detail="The data service didn't answer, so the list of codes can't be shown right now." />;
  }
  if (codes.length === 0) {
    return <p className="text-dim">The data service returned no codes.</p>;
  }
  return (
    <div className="max-w-[72ch] overflow-hidden rounded-[var(--radius-panel)] border border-hairline bg-console">
      <table className="w-full border-collapse text-[14px] leading-5">
        <caption className="sr-only">
          Form 4 transaction codes, what each means, and whether it counts as buying or selling on this site
        </caption>
        <thead className="bg-raised">
          <tr>
            <th scope="col" className={`${th} w-14`}>
              Code
            </th>
            <th scope="col" className={th}>
              Meaning
            </th>
            <th scope="col" className={`${th} w-24`}>
              Buy or sell?
            </th>
          </tr>
        </thead>
        <tbody>
          {codes.map((c) => (
            <tr key={c.code} className="border-t border-hairline">
              <th scope="row" className={`${td} text-left font-mono font-normal`}>
                {c.code}
              </th>
              <td className={td}>{c.label}</td>
              <td className={td}>
                <Counts code={c.code} openMarket={c.open_market} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
