export class FrameTimeline {
  constructor() {
    this.reset();
  }

  reset() {
    this.frames = [];
    this.cursor = -1;
    this.followingLive = true;
  }

  get length() {
    return this.frames.length;
  }

  get lastIndex() {
    return this.frames.length - 1;
  }

  get current() {
    return this.cursor >= 0 ? this.frames[this.cursor] : null;
  }

  get pending() {
    return Math.max(0, this.lastIndex - this.cursor);
  }

  append(payload) {
    this.frames.push(payload);
    if (this.followingLive) {
      this.cursor = this.lastIndex;
      return payload;
    }
    return null;
  }

  seek(index) {
    if (!this.frames.length) return null;
    const numericIndex = Number.isFinite(Number(index)) ? Math.trunc(Number(index)) : 0;
    this.cursor = Math.min(this.lastIndex, Math.max(0, numericIndex));
    this.followingLive = this.cursor === this.lastIndex;
    return this.current;
  }

  hold() {
    this.followingLive = false;
  }

  advance() {
    if (!this.frames.length || this.cursor >= this.lastIndex) return null;
    this.cursor += 1;
    this.followingLive = this.cursor === this.lastIndex;
    return this.current;
  }

  goLive() {
    if (!this.frames.length) return null;
    this.cursor = this.lastIndex;
    this.followingLive = true;
    return this.current;
  }
}
