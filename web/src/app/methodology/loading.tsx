import { DocSkeleton } from "@/components/docs/DocSkeleton";

/** Shown while the page (and its code table) loads. */
export default function Loading() {
  return <DocSkeleton title="Methodology" what="the methodology" />;
}
