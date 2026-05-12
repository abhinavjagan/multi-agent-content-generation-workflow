/**
 * Browser-side voice helpers used by PersonaCreate.tsx.
 *
 * Two surfaces:
 *
 * 1. `MicRecorder` -- a thin wrapper around `MediaRecorder` that exposes
 *    `start()` / `stop()` / `cancel()` and a `level()` getter (0..1) so the
 *    UI can render a live amplitude meter without re-implementing the
 *    AudioContext pipeline. Designed for push-to-talk: one instance per
 *    answer, stop() returns the captured Blob, the underlying tracks are
 *    released the moment we stop.
 *
 * 2. `playWavBlob(...)` -- creates an `<audio>` element backed by an
 *    object URL, plays it, and revokes the URL on `ended` / `error` to
 *    avoid leaks. Returns a handle with `stop()` so the wizard can
 *    cancel TTS when the user starts recording.
 *
 * Security / privacy:
 *  - Microphone is only opened on `start()` (a user gesture), and tracks
 *    are stopped immediately on `stop()` / `cancel()`. We never keep an
 *    open stream after recording ends.
 *  - Captured blobs live only in memory until the caller sends them to
 *    `/api/voice/transcribe`; no localStorage, no disk.
 */

const PREFERRED_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4",
];

export interface MicSupport {
  ok: boolean;
  mimeType: string | null;
  reason?: string;
}

/**
 * Probe browser support without prompting for microphone permission.
 *
 * Returns the first MIME type the runtime will actually accept for
 * MediaRecorder, or `ok: false` with a short reason when the browser
 * is too old to use the voice flow.
 */
export function detectMicSupport(): MicSupport {
  if (typeof window === "undefined") {
    return { ok: false, mimeType: null, reason: "no window" };
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return { ok: false, mimeType: null, reason: "MediaDevices unavailable" };
  }
  if (typeof MediaRecorder === "undefined") {
    return { ok: false, mimeType: null, reason: "MediaRecorder unavailable" };
  }
  for (const t of PREFERRED_MIME_TYPES) {
    try {
      if (MediaRecorder.isTypeSupported(t)) {
        return { ok: true, mimeType: t };
      }
    } catch {
      // Some browsers throw on unknown types; ignore and try the next one.
    }
  }
  return { ok: false, mimeType: null, reason: "no supported MIME type" };
}

export interface RecordedAudio {
  blob: Blob;
  mimeType: string;
  durationMs: number;
}

/**
 * Sentinel error thrown by ``MicRecorder.start()`` when the caller
 * cancelled before the underlying ``MediaRecorder`` was up. Distinct
 * from real failures (denied permission, no codec) so the UI can
 * silently ignore push-to-talk taps that were too brief.
 */
export class MicAborted extends Error {
  constructor() {
    super("aborted");
    this.name = "MicAborted";
  }
}

export class MicRecorder {
  private stream: MediaStream | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: BlobPart[] = [];
  private audioCtx: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  // Backing buffer kept around so we don't reallocate per rAF tick. The
  // typed-array generic must be ``ArrayBuffer`` (not the default
  // ``ArrayBufferLike``) to satisfy ``AnalyserNode.getByteTimeDomainData``.
  private analyserData: Uint8Array<ArrayBuffer> | null = null;
  private startedAt = 0;
  private resolveStop: ((value: RecordedAudio) => void) | null = null;
  private rejectStop: ((reason: Error) => void) | null = null;
  private cancelled = false;
  private chosenMime: string | null = null;
  // Promise the most recent ``start()`` call is waiting on. ``stop()``
  // and ``cancel()`` await this before doing anything, so a tap that
  // releases the mic before getUserMedia has even returned never
  // produces the "recorder not running" race that left the UI stuck.
  private startPromise: Promise<void> | null = null;

  /**
   * Whether ``start()`` has finished and the underlying MediaRecorder
   * is actively capturing. False during the in-flight startup window
   * (getUserMedia permission prompt + codec init).
   */
  isStarted(): boolean {
    return this.recorder !== null;
  }

  /**
   * Start capturing. Throws ``MicAborted`` if ``cancel()`` was called
   * before startup finished; throws a regular Error for genuine
   * failures (denied permission, unsupported codec, ...).
   */
  async start(): Promise<void> {
    if (this.startPromise) return this.startPromise;
    if (this.recorder) {
      throw new Error("MicRecorder is already running");
    }
    if (this.cancelled) {
      // The instance was discarded before start() was ever called.
      throw new MicAborted();
    }
    this.startPromise = this._startInner();
    try {
      await this.startPromise;
    } finally {
      this.startPromise = null;
    }
  }

  private async _startInner(): Promise<void> {
    const support = detectMicSupport();
    if (!support.ok || !support.mimeType) {
      throw new Error(support.reason ?? "voice unsupported in this browser");
    }
    this.chosenMime = support.mimeType;
    // Modest defaults: mono, 16 kHz preferred (Whisper's native rate) but
    // browsers may ignore the hint -- the server downsamples regardless.
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          sampleRate: 16000,
        } as MediaTrackConstraints,
      });
    } catch (err) {
      // Permission denied or device busy. Surface to caller.
      throw err;
    }

    // The user may have released the mic / hit Escape while the
    // permission prompt was up. Drop the stream we just got and bail
    // instead of leaking an open mic to a recorder no one is watching.
    if (this.cancelled) {
      stream.getTracks().forEach((t) => t.stop());
      throw new MicAborted();
    }
    this.stream = stream;

    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, {
        mimeType: this.chosenMime,
        audioBitsPerSecond: 64000,
      });
    } catch (err) {
      this.releaseStream();
      throw err;
    }

    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data);
    };
    recorder.onerror = (e) => {
      const evt = e as Event & { error?: Error };
      this.rejectStop?.(evt.error ?? new Error("MediaRecorder error"));
      this.cleanup();
    };
    recorder.onstop = () => {
      if (this.cancelled) {
        this.cleanup();
        return;
      }
      const mime = this.chosenMime ?? "audio/webm";
      const blob = new Blob(this.chunks, { type: mime });
      const durationMs = Math.max(0, performance.now() - this.startedAt);
      this.resolveStop?.({ blob, mimeType: mime, durationMs });
      this.cleanup();
    };

    // Level meter via Web Audio API. AnalyserNode is cheap; we read on
    // demand from the UI, no extra interval. The window cast keeps us
    // compatible with browsers that only expose the prefixed name.
    try {
      type AudioCtxCtor = typeof AudioContext;
      const w = window as unknown as {
        AudioContext?: AudioCtxCtor;
        webkitAudioContext?: AudioCtxCtor;
      };
      const AudioCtor = w.AudioContext ?? w.webkitAudioContext;
      if (AudioCtor) {
        const ctx = new AudioCtor();
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        source.connect(analyser);
        this.audioCtx = ctx;
        this.analyser = analyser;
        this.analyserData = new Uint8Array(
          new ArrayBuffer(analyser.frequencyBinCount),
        );
      }
    } catch {
      // Level meter is decorative -- failure here must not break recording.
    }

    // Final cancellation check before flipping the live switch. If we
    // got here AND the caller cancelled, fully tear down.
    if (this.cancelled) {
      try {
        recorder.stop();
      } catch {
        // ignore
      }
      this.cleanup();
      throw new MicAborted();
    }

    this.recorder = recorder;
    this.recorder.start();
    this.startedAt = performance.now();
  }

  /**
   * Read the current normalised RMS level in [0, 1]. Returns 0 if the
   * analyser couldn't be set up. Cheap; safe to call inside a rAF loop.
   */
  level(): number {
    if (!this.analyser || !this.analyserData) return 0;
    this.analyser.getByteTimeDomainData(this.analyserData);
    let sum = 0;
    for (let i = 0; i < this.analyserData.length; i++) {
      const v = (this.analyserData[i] - 128) / 128;
      sum += v * v;
    }
    const rms = Math.sqrt(sum / this.analyserData.length);
    return Math.min(1, rms * 1.6);
  }

  /**
   * Stop recording and resolve with the captured audio.
   *
   * If a ``start()`` is still in flight, we await it first so we don't
   * reject with "recorder not running" before the MediaRecorder has
   * even been wired up. If the start was aborted (the caller called
   * ``cancel()`` mid-flight) this resolves with ``null`` via the
   * ``MicAborted`` path -- callers should treat ``MicAborted`` as
   * "user released too quickly" and ignore it.
   */
  async stop(): Promise<RecordedAudio> {
    // Wait for any pending startup so we don't observe a transient
    // null ``recorder`` between getUserMedia and ``new MediaRecorder``.
    if (this.startPromise) {
      try {
        await this.startPromise;
      } catch {
        // start failed / was aborted; the next check handles it.
      }
    }
    return new Promise<RecordedAudio>((resolve, reject) => {
      if (!this.recorder) {
        // Either start() never completed (aborted) or this MicRecorder
        // was never started. Either way: caller's tap was too short
        // to produce audio.
        reject(new MicAborted());
        return;
      }
      this.resolveStop = resolve;
      this.rejectStop = reject;
      try {
        this.recorder.stop();
      } catch (err) {
        reject(err as Error);
        this.cleanup();
      }
    });
  }

  /**
   * Stop and discard the buffer (e.g. user changed their mind, or the
   * pointer drifted off the button). Safe to call before, during, or
   * after ``start()``. After ``cancel()`` the instance is dead --
   * create a fresh ``MicRecorder`` for the next attempt.
   */
  cancel(): void {
    this.cancelled = true;
    if (this.recorder) {
      try {
        this.recorder.stop();
      } catch {
        // ignore
      }
    }
    // If a start is in flight, the cancellation flag will make it bail
    // out and release the stream itself. We still cleanup here so any
    // partially-set state is gone, but we DO NOT touch ``this.stream``
    // if the in-flight start hasn't assigned it yet.
    this.cleanup();
  }

  private releaseStream(): void {
    try {
      this.stream?.getTracks().forEach((t) => t.stop());
    } catch {
      // ignore
    }
    this.stream = null;
  }

  private cleanup(): void {
    this.releaseStream();
    try {
      this.audioCtx?.close();
    } catch {
      // ignore
    }
    this.audioCtx = null;
    this.analyser = null;
    this.analyserData = null;
    this.recorder = null;
    this.chunks = [];
    this.resolveStop = null;
    this.rejectStop = null;
  }
}

export interface PlaybackHandle {
  /** Stop playback and revoke the underlying object URL. */
  stop(): void;
  /** Promise that resolves on natural end or stop(). */
  finished: Promise<void>;
}

/**
 * Play a WAV blob, returning a handle the caller can use to cancel. The
 * object URL is revoked on `ended`, `error`, or explicit `stop()` so we
 * don't leak blob memory across many synthesis calls.
 */
export function playWavBlob(blob: Blob): PlaybackHandle {
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);
  let resolved = false;
  let resolve: () => void = () => {};
  const finished = new Promise<void>((res) => {
    resolve = res;
  });
  const cleanup = () => {
    if (resolved) return;
    resolved = true;
    try {
      audio.pause();
    } catch {
      // ignore
    }
    URL.revokeObjectURL(url);
    resolve();
  };
  audio.addEventListener("ended", cleanup);
  audio.addEventListener("error", cleanup);
  audio.play().catch(cleanup);
  return {
    stop: cleanup,
    finished,
  };
}
