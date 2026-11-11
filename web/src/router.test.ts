import { href, parseHash } from "./router";

test("parses project and home routes", () => {
  expect(parseHash("#/p/abc123def456")).toEqual({ name: "project", id: "abc123def456" });
  expect(parseHash("")).toEqual({ name: "home" });
  expect(parseHash("#/whatever")).toEqual({ name: "home" });
});

test("parses modules and settings routes", () => {
  expect(parseHash("#/modules")).toEqual({ name: "modules" });
  expect(parseHash("#/settings")).toEqual({ name: "settings" });
});

test("href builds hashes", () => {
  expect(href({ name: "project", id: "abc" })).toBe("#/p/abc");
  expect(href({ name: "home" })).toBe("#/");
  expect(href({ name: "modules" })).toBe("#/modules");
  expect(href({ name: "settings" })).toBe("#/settings");
});
