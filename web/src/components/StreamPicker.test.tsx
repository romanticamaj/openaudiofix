import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import type { Project } from "../api";
import { StreamPicker } from "./StreamPicker";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const project = {
  id: "p1", state: "needs_stream",
  streams: [{ index: 0, codec: "aac", sr: 48000, channels: 2 }, { index: 1, codec: "ac3", sr: 48000, channels: 6 }],
} as unknown as Project;

test("Use this track hands the picked index to onChoose and makes no request itself", () => {
  const onChoose = vi.fn();
  const fetchSpy = vi.fn();
  vi.stubGlobal("fetch", fetchSpy);
  render(<StreamPicker project={project} onChoose={onChoose} />);
  fireEvent.click(screen.getAllByRole("radio")[1]);
  fireEvent.click(screen.getByRole("button", { name: "Use this track" }));
  expect(onChoose).toHaveBeenCalledWith(1);
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("the button is disabled while busy", () => {
  render(<StreamPicker project={project} onChoose={() => {}} busy />);
  expect((screen.getByRole("button", { name: "Use this track" }) as HTMLButtonElement).disabled).toBe(true);
});
