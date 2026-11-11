import { act, fireEvent, render, screen } from "@testing-library/react";
import * as apiMod from "../api";
import { FolderPicker, placeName } from "./FolderPicker";

afterEach(() => vi.restoreAllMocks());

test("a place is named by the last part of its path; drives and / keep their whole path", () => {
  expect(placeName("D:\\Work\\Documents")).toBe("Documents");
  expect(placeName("D:\\")).toBe("D:\\");
  expect(placeName("/")).toBe("/");
  expect(placeName("/home/me")).toBe("me");
});

test("a pasted path is opened with Enter", async () => {
  const unc = "\\\\Desktop\\share";
  const dirs = vi.spyOn(apiMod.api, "dirs").mockImplementation(async (p = "") => (
    { path: p || null, parent: null, roots: ["D:\\"], writable: true, dirs: [] }));
  render(<FolderPicker title="Pick" onPick={() => {}} onCancel={() => {}} />);
  const box = await screen.findByLabelText("Current folder");
  fireEvent.change(box, { target: { value: unc } });
  await act(async () => { fireEvent.keyDown(box, { key: "Enter" }); });
  expect(dirs).toHaveBeenLastCalledWith(unc);
  expect((box as HTMLInputElement).value).toBe(unc);
});

test("walks into a folder, makes a new one there and picks it", async () => {
  const listing = (path: string, dirs: string[]) => ({
    path, parent: path === "D:\\" ? null : "D:\\", roots: ["E:\\Home", "D:\\"], writable: true,
    dirs: dirs.map((d) => ({ name: d, path: `${path === "D:\\" ? "D:\\" : `${path}\\`}${d}` })),
  });
  const dirs = vi.spyOn(apiMod.api, "dirs").mockImplementation(async (p = "") =>
    p === "D:\\Mixdown" ? listing("D:\\Mixdown", []) : listing("D:\\", ["Mixdown", "Raw"]));
  const mk = vi.spyOn(apiMod.api, "mkdir").mockResolvedValue(listing("D:\\Mixdown\\EP15", []));
  const onPick = vi.fn();
  render(<FolderPicker title="Pick" initial="D:\\" onPick={onPick} onCancel={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /Mixdown/ }));
  expect(await screen.findByText("No folders here")).toBeTruthy();
  expect(dirs).toHaveBeenLastCalledWith("D:\\Mixdown");
  fireEvent.click(screen.getByRole("button", { name: "New folder" }));
  fireEvent.change(screen.getByLabelText("Folder name"), { target: { value: "EP15" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Create" })); });
  expect(mk).toHaveBeenCalledWith("D:\\Mixdown", "EP15");
  fireEvent.click(screen.getByRole("button", { name: "Choose" }));
  expect(onPick).toHaveBeenCalledWith("D:\\Mixdown\\EP15");
});
