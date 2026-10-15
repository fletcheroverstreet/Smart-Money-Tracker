import Link from "next/link";

import { SearchButton } from "@/components/search/CommandPalette";
import { PageHeader } from "@/components/ui/PageHeader";
import { EmptyState } from "@/components/ui/States";

/** No filer has this address (the API answered 404). */
export default function PersonNotFound() {
  return (
    <div>
      <PageHeader title="Person not found" />
      <EmptyState title="No one on record at this address">
        <p>
          The link may be mistyped, or the filer may not have been loaded yet. Search for the name instead, or browse
          the{" "}
          <Link href="/people" className="text-paper underline decoration-hairline underline-offset-2 hover:decoration-paper">
            insider directory
          </Link>
          .
        </p>
        <div className="mt-4 flex justify-center">
          <SearchButton />
        </div>
      </EmptyState>
    </div>
  );
}
