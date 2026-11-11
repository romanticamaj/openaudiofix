import { fmtBytes, fmtRelative, fmtTime } from "./format";

test("formats time and bytes", () => {
  expect(fmtTime(65.25)).toBe("1:05.3");
  expect(fmtTime(3725)).toBe("1:02:05.0");
  expect(fmtBytes(1536)).toBe("1.5 KB");
  expect(fmtBytes(7.5e9)).toBe("7.5 GB");
});

test("fmtRelative", () => {
  const now = Date.now();
  expect(fmtRelative(now / 1000 - 180, now, "en")).toBe("3 minutes ago");
  expect(fmtRelative(now / 1000 - 180, now, "zh-TW")).toBe("3 分鐘前");
  expect(fmtRelative(now / 1000 + 50, now, "en")).toBe("now");
  expect(fmtRelative(now / 1000 - 7200, now, "en")).toBe("2 hours ago");
  expect(fmtRelative(now / 1000 - 86400 * 3, now, "en")).toBe("3 days ago");
});
