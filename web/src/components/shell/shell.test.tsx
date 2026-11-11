import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, notifyModulesChanged, type ModuleInfo, type Project } from "../../api";
import { GpuStatusCard } from "./GpuStatusCard";
import { ShellContext } from "./ShellContext";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

const STATES = ["done", "ready", "processing", "failed", "done", "needs_stream", "scanning"];
const projects = STATES.map((state, i) => ({ id: `p${i}`, name: `Proj ${i}`, created: 1000 + i, state })) as unknown as Project[];
const gpu = { id: "denoise", name: "Denoise", device: "gpu", state: "installed", step: null, detail: { gpu: "RTX 5070 Ti", speed_x: 13.9 } } as ModuleInfo;

beforeEach(() => {
  vi.spyOn(api, "projects").mockResolvedValue({ projects, total_bytes: 0, free_bytes: 0 });
  vi.spyOn(api, "modules").mockResolvedValue({ modules: [gpu] });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Sidebar", () => {
  it("shows 5 recent projects newest first and a count of 7", async () => {
    render(<Sidebar route={{ name: "home" }} open={false} onClose={() => {}} />);
    await waitFor(() => expect(screen.getAllByRole("link", { name: /Proj \d/ })).toHaveLength(5));
    const links = screen.getAllByRole("link", { name: /Proj \d/ });
    expect(links.map((a) => a.textContent)).toEqual(["Proj 6", "Proj 5", "Proj 4", "Proj 3", "Proj 2"]);
    expect(links[0].getAttribute("href")).toBe("#/p/p6");
    expect(screen.getByRole("link", { name: /Projects/ }).textContent).toContain("7");
  });
  it("marks Projects current on a project route", async () => {
    render(<Sidebar route={{ name: "project", id: "p1" }} open={false} onClose={() => {}} />);
    await waitFor(() => screen.getAllByRole("link", { name: /Proj \d/ }));
    expect(screen.getByRole("link", { name: /^Projects/ }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("aria-current")).toBeNull();
  });
  it("refetches when projects change", async () => {
    render(<Sidebar route={{ name: "home" }} open={false} onClose={() => {}} />);
    await waitFor(() => expect(api.projects).toHaveBeenCalledTimes(1));
    window.dispatchEvent(new Event("afix:projects-changed"));
    await waitFor(() => expect(api.projects).toHaveBeenCalledTimes(2));
  });
  it("updates the GPU card from a modules-changed event without refetching", async () => {
    render(<Sidebar route={{ name: "home" }} open={false} onClose={() => {}} />);
    await screen.findByText("GPU ready");
    notifyModulesChanged({ ...gpu, state: "installing", step: "pull", detail: {} });
    await waitFor(() => expect(screen.queryByText("GPU ready")).toBeNull());
    expect(api.modules).toHaveBeenCalledTimes(1);
  });
  it("labels itself Main navigation", () => {
    render(<Sidebar route={{ name: "home" }} open={false} onClose={() => {}} />);
    expect(screen.getByRole("complementary", { name: "Main navigation" })).toBeTruthy();
  });
  it("closes the drawer on any link click, even to the current hash", async () => {
    const onClose = vi.fn();
    const { container } = render(<Sidebar route={{ name: "home" }} open={true} onClose={onClose} />);
    await waitFor(() => screen.getAllByRole("link", { name: /Proj \d/ }));
    const links = Array.from(container.querySelectorAll("a"));
    expect(links.length).toBe(1 + 3 + 5 + 1);
    links.forEach((a) => fireEvent.click(a));
    expect(onClose).toHaveBeenCalledTimes(links.length);
  });
});

describe("GpuStatusCard", () => {
  it("shows ready and detail", () => {
    render(<GpuStatusCard module={gpu} />);
    expect(screen.getByText("GPU ready")).toBeTruthy();
    expect(screen.getByText("RTX 5070 Ti · 13.9× realtime")).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe("#/modules");
  });
  it("shows not installed when undefined", () => {
    render(<GpuStatusCard module={undefined} />);
    expect(screen.getByText("Background repair not installed")).toBeTruthy();
  });
});

describe("Topbar", () => {
  it("hamburger calls openNav; crumbs link except last", () => {
    const openNav = vi.fn();
    render(<ShellContext.Provider value={{ openNav }}><Topbar crumbs={[<a key="a" href="#/">Projects</a>, "Now"]} /></ShellContext.Provider>);
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    expect(openNav).toHaveBeenCalled();
    expect(screen.getByText("Now").closest("a")).toBeNull();
  });
});
