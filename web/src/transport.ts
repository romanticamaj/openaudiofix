export const PREROLL = 1.0;
const MERGE = 0.25;

export interface MediaLike {
  currentTime: number;
  paused: boolean;
  play(): unknown;
  pause(): void;
  addEventListener(type: string, f: () => void): void;
}

export class Transport {
  dur = 0;
  private els = new Map<string, MediaLike>();
  private _cur = "";
  private fixes: number[] = [];
  private subs = new Set<() => void>();
  private raf = 0;

  constructor(private make: (url: string) => MediaLike) {}

  setSources(list: { id: string; url: string }[], initial: string) {
    for (const el of this.els.values()) el.pause();
    this.els.clear();
    for (const s of list) {
      const el = this.make(s.url);
      el.addEventListener("play", () => this.tick());
      for (const ev of ["pause", "ended", "loadedmetadata", "seeked"]) el.addEventListener(ev, () => this.emit());
      this.els.set(s.id, el);
    }
    this._cur = this.els.has(initial) ? initial : list[0]?.id ?? "";
    this.emit();
  }

  setFixes(seconds: number[]) {
    const ts = [...seconds].sort((a, b) => a - b);
    this.fixes = ts.filter((t, i) => i === 0 || t - ts[i - 1] > MERGE);
    this.emit();
  }

  get cur() { return this._cur; }
  get fixCount() { return this.fixes.length; }
  /** The i-th jump point (sorted, merged), in seconds. */
  fixAt(i: number): number | undefined { return this.fixes[i]; }
  private get a() { return this.els.get(this._cur); }
  get t() { return this.a?.currentTime ?? 0; }
  get playing() { return !!this.a && !this.a.paused; }
  has(id: string) { return this.els.has(id); }

  select(id: string) {
    if (!this.els.has(id) || id === this._cur) return;
    const playing = this.playing, t = this.t;
    this.a?.pause();
    this._cur = id;
    this.a!.currentTime = t;
    if (playing) this.a!.play();
    this.emit();
  }

  toggle() {
    if (!this.a) return;
    if (this.a.paused) this.a.play(); else this.a.pause();
    this.emit();
  }

  seek(t: number) {
    if (!this.a) return;
    this.a.currentTime = Math.max(0, Math.min(this.dur, t));
    this.emit();
  }

  seekToFix(f: number) { this.seek(f - PREROLL); }

  next() {
    const t = this.t;
    const f = this.fixes.find((x) => x - PREROLL > t + 0.05);
    if (f != null) this.seekToFix(f);
  }

  prev() {
    const t = this.t;
    let f: number | null = null;
    for (const x of this.fixes) { if (x - PREROLL < t - 0.3) f = x; else break; }
    if (f != null) this.seekToFix(f);
  }

  fixIndex() {
    const t = this.t;
    return this.fixes.filter((x) => x <= t + PREROLL + 0.05).length;
  }

  subscribe(f: () => void) { this.subs.add(f); return () => { this.subs.delete(f); }; }
  private emit() { for (const f of this.subs) f(); }

  private tick() {
    cancelAnimationFrame(this.raf);
    const step = () => { this.emit(); if (this.playing) this.raf = requestAnimationFrame(step); };
    step();
  }

  destroy() {
    cancelAnimationFrame(this.raf);
    for (const el of this.els.values()) el.pause();
    this.els.clear();
    this.subs.clear();
  }
}
