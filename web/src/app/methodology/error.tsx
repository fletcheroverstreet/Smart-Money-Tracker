"use client";

import { DocError } from "@/components/docs/DocError";

export default function MethodologyError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return <DocError what="Methodology" digest={error.digest} retry={retry} />;
}
