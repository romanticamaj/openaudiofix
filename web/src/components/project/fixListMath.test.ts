import { filterFixes, fixKinds, visibleRange, ROW_H, VIRTUAL_THRESHOLD } from "./fixListMath";

const f = (module: string, seconds: number) => ({ module, kind: "x", channel: -1, start: seconds * 48000, end: seconds * 48000 + 48, seconds });

test("filterFixes keeps only the chosen module; all keeps everything", () => {
  const list = [f("declip", 1), f("glitch", 2), f("declip", 3)];
  expect(filterFixes(list, "declip").map((x) => x.seconds)).toEqual([1, 3]);
  expect(filterFixes(list, "all")).toBe(list);
  expect(filterFixes(list, "denoise")).toEqual([]);
});

test("fixKinds lists the modules present in the order glitch, declip, denoise", () => {
  expect(fixKinds([f("denoise", 0), f("declip", 1), f("glitch", 2), f("declip", 3)])).toEqual(["glitch", "declip", "denoise"]);
  expect(fixKinds([f("declip", 1)])).toEqual(["declip"]);
  expect(fixKinds([])).toEqual([]);
});

test("visibleRange covers the viewport plus overscan, clamped to the list", () => {
  expect(visibleRange(0, 640, 32, 5000)).toEqual([0, 28]);
  expect(visibleRange(32000, 640, 32, 5000)).toEqual([992, 1028]);
  expect(visibleRange(32 * 4995, 640, 32, 5000)).toEqual([4987, 5000]);
  expect(visibleRange(0, 640, 32, 3)).toEqual([0, 3]);
});

test("constants", () => {
  expect(VIRTUAL_THRESHOLD).toBe(2000);
  expect(ROW_H).toBe(32);
});
