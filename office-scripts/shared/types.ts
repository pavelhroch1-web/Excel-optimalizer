// Dev-source reference, duplicated into each deployable script (see README.md).
// Authoritative schema also documented in docs/DATA_MODEL.md - keep both in sync.

type POSStatus = "Active" | "Closed";
type ManagerOverrideType = "" | "FORCE_INCLUDE" | "FORCE_EXCLUDE";

interface POSMasterRecord {
  // Identity
  posId: string;
  terminalId: string;

  // Imported (RAW_DATA) - overwritten wholesale on every Import Engine run
  market: string;
  category: string;
  terminalType: string;
  classification: string; // KATEGORIZACE: A / B / P
  nazev: string; // NAZEV PROVOZOVNY - store/outlet name
  area: string; // OBLAST - region, e.g. "Praha-vychod"
  posArea: string; // POS AREA - sales-area code, e.g. "RSA"
  street: string;
  houseNumber: string;
  city: string;
  gpsX: number;
  gpsY: number;
  assignedTechnician: string;
  ppt: number;

  // Status (from POS_STATUS_IMPORT only - never inferred from RAW_DATA absence)
  status: POSStatus;
  closedSinceWeek: number | null;
  closedSinceYear: number | null;

  // Campaign state - derived, recomputed by Planning Engine (not Import Engine)
  currentLosActivity: string;
  currentLotActivity: string;
  targetLosActivity: string;
  targetLotActivity: string;

  // Visit facts - derived, recomputed once Compliance Engine exists
  lastRealVisitDate: string | null;
  lastRealVisitWeek: number | null;
  lastPlannedVisitDate: string | null;
  weeksSinceLastVisit: number | null;
  visitCountThisCampaign: number;

  // Scoring - written by Business Engine (not built yet)
  businessScore: number | null;

  // Decision metadata - written by Decision/Route Engine (not built yet)
  plannerStatus: string;
  assignedWeek: number | null;
  assignedDay: string;
  gpsGroup: number | null;

  // Manual layer - NEVER overwritten by Import Engine or any engine
  managerOverrideType: ManagerOverrideType;
  managerOverridePriority: string;
  managerOverrideTechnician: string;
  plannerNotes: string;

  // Bookkeeping
  importedAt: string;
  updatedAt: string;
}

// Imported and stored, but per product-owner decision NOT read by any engine yet
// (see docs/BUSINESS_RULES.md 15b/15c - V10.5.5 never read these columns either,
// so there is no proven mechanism to preserve; reserved as an optional future
// extension point for campaign priority / minimum-gap override).
interface ActivityPlanEntry {
  activityType: "LOS" | "LOT";
  activity: string;
  startWeek: number;
  endWeek: number;
  priority: number | null; // reserved, unused
  overrideGapWeeks: number | null; // reserved, unused
}
