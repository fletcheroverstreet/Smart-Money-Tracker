import { DocSkeleton } from "@/components/docs/DocSkeleton";

/** Shown while the page (and its source list) loads. */
export default function Loading() {
  return <DocSkeleton title="Sources" what="the list of sources" />;
}
