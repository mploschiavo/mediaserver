import { AuditEventsChart } from "./AuditEventsChart";
import { AuditLogTable } from "./AuditLogTable";
import { IntegrityBanner } from "./IntegrityBanner";
import { RetentionCard } from "./RetentionCard";

/**
 * Composes the audit-log feature into a single column: the tamper-
 * evident integrity banner up top, retention stats, the activity
 * chart pair (events/hour + actor split), then the filterable row
 * table. The route file owns the outer ``max-w-6xl`` page-shell +
 * PageHeader + entrance animation; this component only paints the
 * in-column composition.
 */
export function AuditLogPage() {
  return (
    <div className="flex flex-col gap-6" data-testid="audit-log-page">
      <IntegrityBanner />
      <RetentionCard />
      <AuditEventsChart />
      <AuditLogTable />
    </div>
  );
}
