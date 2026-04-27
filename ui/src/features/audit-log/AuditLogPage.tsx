import { AuditLogTable } from "./AuditLogTable";
import { IntegrityBanner } from "./IntegrityBanner";
import { RetentionCard } from "./RetentionCard";

/**
 * Composes the audit-log feature into a single column: the
 * tamper-evident integrity banner sits at the top, the retention
 * stats card underneath, then the row table (with filter + limit
 * controls). Layout mirrors the media-integrity reference page so
 * the observability surfaces stay visually consistent. The route
 * file owns the outer `max-w-6xl` page-shell + PageHeader +
 * entrance animation; this component only paints the in-column
 * composition.
 */
export function AuditLogPage() {
  return (
    <div className="flex flex-col gap-6" data-testid="audit-log-page">
      <IntegrityBanner />
      <RetentionCard />
      <AuditLogTable />
    </div>
  );
}
