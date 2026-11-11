import type { Project } from "./api";
import { filterProjects, groupCounts, sortProjects } from "./projectFilter";

const mk = (id: string, name: string, state: string) => ({ id, name, state }) as Project;
const list = [
  mk("a", "interview.wav", "done"), mk("b", "Song.mp3", "scanning"), mk("c", "talk.flac", "processing"),
  mk("d", "x.wav", "ready"), mk("e", "y.wav", "needs_stream"), mk("f", "z.wav", "failed"), mk("g", "w.wav", "cancelled"),
];

test("search is a case-insensitive substring on the trimmed name", () => {
  expect(filterProjects(list, "  INTER ", "all").map((p) => p.id)).toEqual(["a"]);
  expect(filterProjects(list, "", "all")).toHaveLength(7);
});

test("busy group contains scanning and processing", () => {
  expect(filterProjects(list, "", "busy").map((p) => p.id)).toEqual(["b", "c"]);
  expect(filterProjects(list, "", "failed").map((p) => p.id)).toEqual(["f", "g"]);
});

test("search and group combine", () => {
  expect(filterProjects(list, "wav", "waiting").map((p) => p.id)).toEqual(["d", "e"]);
});

test("groupCounts totals equal the list length", () => {
  const c = groupCounts(list);
  expect(c).toEqual({ all: 7, done: 1, busy: 2, waiting: 2, failed: 2 });
  expect(c.done + c.busy + c.waiting + c.failed).toBe(list.length);
});

test("sorting: by processing time biggest first, unfinished last; by name A to Z; ties newest first", () => {
  const p = (id: string, name: string, run: number | null, created: number) =>
    ({ id, name, state: run == null ? "processing" : "done", run_s: run, created, size_bytes: 0 }) as Project;
  const l = [p("a", "beta", 10, 1), p("b", "Alpha", null, 2), p("c", "gamma", 30, 3), p("d", "delta", 10, 4)];
  expect(sortProjects(l, { key: "run", dir: "desc" }).map((x) => x.id)).toEqual(["c", "d", "a", "b"]);
  expect(sortProjects(l, { key: "run", dir: "asc" }).map((x) => x.id)).toEqual(["d", "a", "c", "b"]);
  expect(sortProjects(l, { key: "name", dir: "asc" }).map((x) => x.id)).toEqual(["b", "a", "d", "c"]);
  expect(sortProjects(l, { key: "created", dir: "desc" }).map((x) => x.id)).toEqual(["d", "c", "b", "a"]);
});
