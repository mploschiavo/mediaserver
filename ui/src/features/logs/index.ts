// Public re-exports for the Logs feature surface. Routes import from
// this barrel; helpers + hooks stay private to the feature unless
// explicitly re-exported.

export { LogsPage } from "./LogsPage";
export { LogsToolbar, ALL_SOURCES } from "./LogsToolbar";
export { LogsTable } from "./LogsTable";
export {
  parseLogLine,
  useMultiLogs,
  LEVELS,
} from "./hooks";
export type { LevelTag, ParsedLine } from "./hooks";
export {
  extractTimestamp,
  hashSource,
  parseSearch,
  SOURCE_TONE_COUNT,
} from "./format";
export type { HighlightSegment, ParsedSearch } from "./format";
