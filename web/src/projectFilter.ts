import type { Project } from "./api";
import { stateGroup, type Group } from "./status";

export type GroupFilter = Group | "all";

export function filterProjects(list: Project[], q: string, group: GroupFilter): Project[] {
  const needle = q.trim().toLowerCase();
  return list.filter((p) =>
    (group === "all" || stateGroup(p.state) === group) &&
    (needle === "" || p.name.toLowerCase().includes(needle)));
}

export function groupCounts(list: Project[]): Record<GroupFilter, number> {
  const c: Record<GroupFilter, number> = { all: list.length, done: 0, busy: 0, waiting: 0, failed: 0 };
  for (const p of list) c[stateGroup(p.state)]++;
  return c;
}

export type SortKey = "name" | "state" | "duration" | "fixes" | "run" | "size" | "created";
export type Sort = { key: SortKey; dir: "asc" | "desc" };

const GROUP_ORDER = ["busy", "waiting", "failed", "done"];

function sortValue(p: Project, key: SortKey, fixCount: (p: Project) => number): number | string | null {
  switch (key) {
    case "name": return p.name.toLocaleLowerCase();
    case "state": return GROUP_ORDER.indexOf(stateGroup(p.state));
    case "duration": return p.duration_s ?? null;
    case "fixes": return p.state === "done" ? fixCount(p) : null;
    case "run": return p.run_s ?? null;
    case "size": return p.size_bytes ?? 0;
    default: return p.created;
  }
}

/** Sorted copy; projects without a value (not finished yet) always go last, newest first breaks ties. */
export function sortProjects(list: Project[], sort: Sort, fixCount: (p: Project) => number = () => 0): Project[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return [...list].sort((a, b) => {
    const x = sortValue(a, sort.key, fixCount), y = sortValue(b, sort.key, fixCount);
    if (x == null || y == null) {
      if (x == null && y == null) return b.created - a.created;
      return x == null ? 1 : -1;
    }
    const c = typeof x === "string" ? x.localeCompare(y as string) : (x as number) - (y as number);
    return c !== 0 ? c * sign : b.created - a.created;
  });
}
