"use strict";

// GitHub's rolling API window refreshes daily records. It never replaces the
// complete saved history or serves as a value to add directly to the total.
const METRICS = [
  ["clones", "totalClones"],
  ["views", "totalViews"],
  ["uniqueClones", "totalUniqueClones"],
  ["uniqueViews", "totalUniqueViews"],
  ["organicClones", "totalOrganicClones"],
  ["organicUniqueClones", "totalOrganicUniqueClones"],
];

function dateKey(value) {
  const key = String(value).slice(0, 10);
  const parsed = new Date(`${key}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key) || !Number.isFinite(parsed.getTime()) ||
      parsed.toISOString().slice(0, 10) !== key) {
    throw new Error(`Invalid traffic date: ${value}`);
  }
  return key;
}

function count(value) {
  if (value == null) return 0;
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`Invalid traffic count: ${value}`);
  }
  return value;
}

function historySum(state, field) {
  return state.dailyHistory.reduce((sum, entry) => sum + count(entry[field]), 0);
}

function prepareHistory(state) {
  const raw = state.dailyHistory ?? [];
  const rows = Array.isArray(raw) ? raw : Object.values(raw);
  const byDate = new Map();
  for (const row of rows) {
    const key = dateKey(row.date);
    const previous = byDate.get(key) ?? {};
    const merged = { ...previous, ...row, date: `${key}T00:00:00Z` };
    for (const [field] of METRICS) {
      if (previous[field] != null && row[field] != null) {
        merged[field] = Math.max(count(previous[field]), count(row[field]));
      } else if (previous[field] != null) {
        merged[field] = previous[field];
      }
    }
    for (const kind of ["clones", "views"]) {
      if (row[kind] == null && previous[kind] != null) {
        merged[`${kind}Status`] = previous[`${kind}Status`];
        merged[`${kind}FetchedAt`] = previous[`${kind}FetchedAt`];
      }
    }
    byDate.set(key, merged);
  }
  state.dailyHistory = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));

  // Preserve cumulative data from an older installation that already trimmed
  // its daily records. This offset is not a fabricated set of daily counts.
  state.historyOffsets ??= {};
  for (const [field, total] of METRICS) {
    if (state.historyOffsets[field] == null) {
      state.historyOffsets[field] = Math.max(0, count(state[total]) - historySum(state, field));
    }
    count(state.historyOffsets[field]);
  }

  for (const entry of state.dailyHistory) {
    const key = dateKey(entry.date);
    for (const kind of ["clones", "views"]) {
      if (entry[`${kind}Status`]) continue;
      const seen = kind === "clones" ? state.lastSeenCloneCounts : state.lastSeenViewCounts;
      if (seen && Object.hasOwn(seen, key)) {
        entry[`${kind}Status`] = "reported";
      } else if (count(entry[kind]) > 0) {
        entry[`${kind}Status`] = "legacy";
      } else {
        // Old workflows wrote today's placeholder as zero without API evidence.
        entry[kind] = null;
        entry[kind === "clones" ? "uniqueClones" : "uniqueViews"] = null;
        entry[`${kind}Status`] = "pending";
      }
    }
  }
  return state;
}

function mergeApiRows(state, kind, rows, capturedAt, byDate) {
  if (rows == null) return; // Endpoint failure: preserve previously known data.
  if (!Array.isArray(rows)) throw new Error(`Invalid ${kind} response`);
  const clone = kind === "clones";
  const unique = clone ? "uniqueClones" : "uniqueViews";
  const seenKey = clone ? "lastSeenCloneCounts" : "lastSeenViewCounts";
  const uniqueSeenKey = clone ? "_lastSeenUniqueClones" : "_lastSeenUniqueViews";
  state[seenKey] ??= {};
  state[uniqueSeenKey] ??= {};
  let lastDate = null;
  for (const row of rows) {
    const key = dateKey(row.timestamp);
    if (key > dateKey(capturedAt)) throw new Error("Traffic API returned a future date");
    if (row.count == null || row.uniques == null) throw new Error("Traffic API row has no count/uniques");
    const value = count(row.count), uniques = count(row.uniques);
    if (uniques > value) throw new Error("Unique traffic exceeds total traffic");
    const entry = byDate.get(key) ?? { date: `${key}T00:00:00Z` };
    // Repeated snapshots and stale lower values must not add a day twice.
    entry[kind] = Math.max(count(entry[kind]), value);
    entry[unique] = Math.max(count(entry[unique]), uniques);
    entry[`${kind}Status`] = "reported";
    entry[`${kind}FetchedAt`] = capturedAt;
    entry.capturedAt = capturedAt;
    byDate.set(key, entry);
    state[seenKey][key] = entry[kind];
    state[uniqueSeenKey][key] = entry[unique];
    if (lastDate == null || key > lastDate) lastDate = key;
  }
  state[clone ? "lastCloneCollectionAt" : "lastViewCollectionAt"] = capturedAt;
  if (lastDate) state[clone ? "lastCloneApiDate" : "lastViewApiDate"] = lastDate;
  // These are only lookup caches. Full daily records are retained separately.
  for (const key of Object.keys(state[seenKey]).sort().slice(0, -30)) {
    delete state[seenKey][key];
    delete state[uniqueSeenKey][key];
  }
  state[clone ? "lastSeenDates" : "lastSeenViewDates"] = rows.map(row => row.timestamp);
}

function mergeHistory(state, { clones = null, views = null }, capturedAt) {
  const today = dateKey(capturedAt);
  const byDate = new Map(state.dailyHistory.map(entry => [dateKey(entry.date), entry]));
  mergeApiRows(state, "clones", clones, capturedAt, byDate);
  mergeApiRows(state, "views", views, capturedAt, byDate);
  const first = [...byDate.keys()].sort()[0] ?? today;
  if (first > today) throw new Error("Saved history starts in the future");
  for (let day = new Date(`${first}T00:00:00Z`); dateKey(day.toISOString()) <= today;
       day.setUTCDate(day.getUTCDate() + 1)) {
    const key = dateKey(day.toISOString());
    const entry = byDate.get(key) ?? { date: `${key}T00:00:00Z` };
    for (const kind of ["clones", "views"]) {
      if (entry[kind] == null) {
        entry[kind] = null;
        entry[kind === "clones" ? "uniqueClones" : "uniqueViews"] = null;
        entry[`${kind}Status`] = "pending";
      }
    }
    entry.downloads ??= 0;
    entry.total = count(entry.clones) + count(entry.downloads);
    byDate.set(key, entry);
  }
  state.dailyHistory = [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
  state.lastCollectionAt = capturedAt;
  state.trafficHistoryVersion = 1;
  return byDate.get(today);
}

function refreshTotals(state) {
  for (const [field, total] of METRICS) {
    state[total] = count(state.historyOffsets[field]) + historySum(state, field);
  }
  // Unique fields retain sums of daily unique counts; they are not a
  // cross-day deduplicated number of people. The public curve uses clones.
  return state;
}

module.exports = { prepareHistory, mergeHistory, refreshTotals };
