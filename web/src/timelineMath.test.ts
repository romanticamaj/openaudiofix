import { clampView, follow, pan, sliceOverview, timeToX, xToTime, zoomAround } from "./timelineMath";

test("zoom keeps the point under the cursor fixed", () => {
  const v = zoomAround({ t0: 0, t1: 100 }, 25, 0.5, 100);
  expect(v).toEqual({ t0: 12.5, t1: 62.5 });
  expect(timeToX(25, v, 1000)).toBeCloseTo(250);
  expect(xToTime(250, v, 1000)).toBeCloseTo(25);
});

test("views stay inside the file", () => {
  expect(clampView({ t0: -10, t1: 20 }, 100)).toEqual({ t0: 0, t1: 30 });
  expect(pan({ t0: 90, t1: 99 }, 5, 100)).toEqual({ t0: 91, t1: 100 });
  expect(clampView({ t0: 5, t1: 5.001 }, 100).t1 - 5).toBeCloseTo(0.05);
});

test("overview slice and follow", () => {
  const peaks = Array.from({ length: 200 }, (_, i) => i);
  expect(sliceOverview(peaks, 100, { t0: 50, t1: 100 })).toEqual(peaks.slice(100, 200));
  expect(follow({ t0: 0, t1: 10 }, 15, 100)).toEqual({ t0: 14, t1: 24 });
  expect(follow({ t0: 0, t1: 10 }, 5, 100)).toEqual({ t0: 0, t1: 10 });
});
