/**
 * When a row was detected, for the Live feed and the Downlink. The feed spans
 * several days, so a clock time alone would put yesterday's "11:41 PM" under
 * today's "7:57 AM" as if they were the same day. Today's rows show the time;
 * older rows add "Yesterday" or "Sep 24" on a line above it (the column stays
 * narrow). The full date and time is the cell's title and its screen-reader
 * text.
 */
import { dayLabelET, fullDateTimeET, timeET } from "@/lib/format";

export function DetectedTime({
  iso,
  today,
  inline = false,
}: {
  iso: string | null | undefined;
  /** Today's Eastern date (useTodayET), or null before the clock is known. */
  today: string | null;
  /** One line ("Sep 24, 11:41 PM") instead of the day stacked above the time. */
  inline?: boolean;
}) {
  if (!iso) return <span>–</span>;
  const time = timeET(iso).replace(" ET", "");
  const dayLabel = dayLabelET(iso, today);
  const full = fullDateTimeET(iso);
  return (
    <time dateTime={iso} title={full}>
      <span aria-hidden className={inline ? undefined : "flex flex-col leading-4"}>
        {dayLabel ? <span className={inline ? undefined : "text-[12px]"}>{inline ? `${dayLabel}, ` : dayLabel}</span> : null}
        <span>{time}</span>
      </span>
      <span className="sr-only">{full}</span>
    </time>
  );
}
