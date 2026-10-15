"use client";

import { DocError } from "@/components/docs/DocError";

export default function SourcesError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <DocError what="Sources" digest={error.digest} retry={retry} />;
}
