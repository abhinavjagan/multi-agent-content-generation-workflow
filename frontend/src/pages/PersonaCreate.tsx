import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AudioLines,
  CheckCircle2,
  CornerUpLeft,
  Loader2,
  Megaphone,
  MessageSquare,
  Mic,
  MicOff,
  Save,
  ShieldCheck,
  SkipForward,
  Sparkles,
  Square,
  Volume2,
  Wand2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  ApiError,
  getHealth,
  speak,
  startInterview,
  submitAnswer,
  transcribe,
} from "@/lib/api";
import type { InterviewMode, InterviewState } from "@/lib/types";
import {
  MicAborted,
  MicRecorder,
  type PlaybackHandle,
  type RecordedAudio,
  detectMicSupport,
  playWavBlob,
} from "@/lib/voice";

interface ConsentForm {
  name: string;
  isReal: boolean;
  handle: string;
  disclosure: string;
  mode: InterviewMode;
  voiceMode: boolean;
  consentAck: boolean;
}

type Stage =
  | { kind: "consent" }
  | { kind: "interview"; state: InterviewState }
  | { kind: "done"; state: InterviewState };

interface PersistedInterview {
  form: ConsentForm;
  state: InterviewState;
  answer: string;
  savedAt: string;
}

// Bumped to v2 when the form shape grew `mode` + `voiceMode`. The migration
// is best-effort: a v1 blob gets discarded silently if it can't be read.
const INTERVIEW_KEY = "x-agent:interview:v2";

function loadPersistedInterview(): PersistedInterview | null {
  try {
    const raw = window.localStorage.getItem(INTERVIEW_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedInterview;
    if (parsed && parsed.state && parsed.form) return parsed;
  } catch {
    // ignore corrupt blob
  }
  return null;
}

function savePersistedInterview(payload: PersistedInterview): void {
  try {
    window.localStorage.setItem(INTERVIEW_KEY, JSON.stringify(payload));
  } catch {
    // privacy mode / quota
  }
}

function clearPersistedInterview(): void {
  try {
    window.localStorage.removeItem(INTERVIEW_KEY);
  } catch {
    // ignore
  }
}

export default function PersonaCreate() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const persisted = loadPersistedInterview();

  // Voice support probe -- pure capability test, never prompts for mic.
  const micSupportRef = useRef(detectMicSupport());

  // /api/health gives us voice readiness flags (engine cache present?).
  // Refetched on mount; we degrade the UI to text-only if voice is not
  // configured / not enabled / not ready on the server.
  const health = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchOnWindowFocus: false,
  });
  const voiceServerReady =
    !!health.data?.voice?.enabled &&
    !!health.data?.voice?.tts_ready &&
    !!health.data?.voice?.stt_ready;
  const voiceServerKnown = !!health.data?.voice;

  const [form, setForm] = useState<ConsentForm>(
    () =>
      persisted?.form ?? {
        name: "",
        isReal: true,
        handle: "",
        disclosure: "",
        mode: "default",
        voiceMode: micSupportRef.current.ok,
        consentAck: false,
      },
  );
  const [stage, setStage] = useState<Stage>({ kind: "consent" });
  const [answer, setAnswer] = useState(persisted?.answer ?? "");
  const [showResumePrompt, setShowResumePrompt] = useState(!!persisted);

  // Voice-mode runtime state. Only meaningful when `form.voiceMode` is on.
  const recorderRef = useRef<MicRecorder | null>(null);
  const playbackRef = useRef<PlaybackHandle | null>(null);
  const lastSpokenQuestionRef = useRef<string | null>(null);
  // ``preparing`` covers the in-flight startup window (getUserMedia
  // permission prompt + MediaRecorder init). The mic button shows a
  // spinner during this state, and a release in this window cancels
  // cleanly instead of trying to call stop() on a recorder that hasn't
  // been wired up yet.
  const [preparing, setPreparing] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [micLevel, setMicLevel] = useState(0);
  const [voiceError, setVoiceError] = useState<string | null>(null);

  const stopTts = useCallback(() => {
    playbackRef.current?.stop();
    playbackRef.current = null;
    setSpeaking(false);
  }, []);

  // Cleanup on unmount: stop any open mic stream and any TTS playback.
  useEffect(() => {
    return () => {
      recorderRef.current?.cancel();
      recorderRef.current = null;
      playbackRef.current?.stop();
      playbackRef.current = null;
    };
  }, []);

  // Auto-fill the disclosure tag from the handle, but only while the user
  // hasn't typed their own custom tag.
  useEffect(() => {
    if (!form.isReal) return;
    const auto = form.handle
      ? `[AI persona of @${form.handle.replace(/^@/, "")}]`
      : form.name
        ? `[AI persona of ${form.name}]`
        : "";
    setForm((f) =>
      !f.disclosure || f.disclosure.startsWith("[AI persona of ")
        ? { ...f, disclosure: auto }
        : f,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.handle, form.name, form.isReal]);

  // Keep the in-progress answer + state mirrored to localStorage so a refresh
  // or accidental tab close doesn't lose typed text.
  useEffect(() => {
    if (stage.kind !== "interview") return;
    savePersistedInterview({
      form,
      state: stage.state,
      answer,
      savedAt: new Date().toISOString(),
    });
  }, [answer, form, stage]);

  const handleResume = () => {
    if (!persisted) return;
    setShowResumePrompt(false);
    setStage({ kind: "interview", state: persisted.state });
    setAnswer(persisted.answer ?? "");
  };

  const handleDiscardSaved = () => {
    clearPersistedInterview();
    setShowResumePrompt(false);
    setAnswer("");
  };

  const handleSaveAndExit = () => {
    if (stage.kind === "interview") {
      savePersistedInterview({
        form,
        state: stage.state,
        answer,
        savedAt: new Date().toISOString(),
      });
    }
    toast.success("Interview saved", {
      description:
        "Your progress is saved locally and on disk under ~/.x-agent/personas. Reopen this page to resume.",
    });
    navigate("/personas");
  };

  const startMutation = useMutation({
    mutationFn: () =>
      startInterview({
        name: form.name.trim(),
        is_real_person: form.isReal,
        disclosure_text: form.isReal ? form.disclosure.trim() : "",
        consent_ack: form.isReal ? form.consentAck : false,
        // Send both for back-compat -- the server prefers `mode`.
        quick: form.mode === "quick",
        mode: form.mode,
      }),
    onSuccess: (state) => {
      if (state.error) {
        toast.error(state.error);
      }
      if (state.saved) {
        clearPersistedInterview();
        setStage({ kind: "done", state });
      } else {
        savePersistedInterview({
          form,
          state,
          answer: "",
          savedAt: new Date().toISOString(),
        });
        setStage({ kind: "interview", state });
      }
      setAnswer("");
    },
    onError: (err) => {
      toast.error(
        err instanceof ApiError ? err.detail : (err as Error).message,
        {
          description:
            "Check that Ollama is reachable and that the FastAPI server is up.",
        },
      );
    },
  });

  const answerMutation = useMutation({
    mutationFn: (input: { threadId: string; answer: string }) =>
      submitAnswer(input.threadId, input.answer),
    onSuccess: (state) => {
      if (state.error) toast.error(state.error);
      if (state.saved) {
        clearPersistedInterview();
        qc.invalidateQueries({ queryKey: ["personas"] });
        qc.invalidateQueries({ queryKey: ["health"] });
        setStage({ kind: "done", state });
      } else {
        savePersistedInterview({
          form,
          state,
          answer: "",
          savedAt: new Date().toISOString(),
        });
        setStage({ kind: "interview", state });
      }
      setAnswer("");
    },
    onError: (err) => {
      const status = err instanceof ApiError ? err.status : 0;
      const message =
        err instanceof ApiError ? err.detail : (err as Error).message;
      const description =
        status === 404
          ? "The server lost this interview's checkpoint (likely a restart). Start over — your previous answers are saved to ~/.x-agent/personas as a transcript."
          : "Your answer is preserved locally; retry or hit Save & continue later.";
      toast.error(message, { description });
    },
  });

  // -------------------------------------------------- voice mode helpers
  const voiceModeAvailable =
    micSupportRef.current.ok &&
    voiceServerKnown &&
    voiceServerReady;

  const voiceActive = form.voiceMode && voiceModeAvailable;

  const submitTranscript = useCallback(
    (threadId: string, text: string) => {
      const trimmed = text.trim();
      if (!trimmed) {
        toast.message("Nothing was transcribed", {
          description:
            "Try again, speak a bit louder, or type your answer instead.",
        });
        return;
      }
      answerMutation.mutate({ threadId, answer: trimmed });
    },
    [answerMutation],
  );

  const handleStartRecording = useCallback(async () => {
    if (recording || preparing || transcribing) return;
    setVoiceError(null);
    stopTts();
    const rec = new MicRecorder();
    recorderRef.current = rec;
    setPreparing(true);
    try {
      await rec.start();
      // The user might have released / cancelled while we were
      // awaiting getUserMedia (the permission prompt is async). In
      // that case ``recorderRef`` was nulled by handleStopRecording /
      // handleCancelRecording, and the underlying MicRecorder already
      // released its stream via the cancellation flag. Don't flip
      // ``recording`` true -- there's nothing to record anymore.
      if (recorderRef.current !== rec) return;
      setRecording(true);
    } catch (err) {
      if (recorderRef.current === rec) recorderRef.current = null;
      // MicAborted == user released before the mic was ready. Silent.
      if (err instanceof MicAborted) return;
      const msg =
        err instanceof Error ? err.message : "Could not open microphone";
      setVoiceError(msg);
      toast.error("Microphone unavailable", {
        description:
          msg + " — you can still type your answer in the box below.",
      });
    } finally {
      setPreparing(false);
    }
  }, [recording, preparing, transcribing, stopTts]);

  const handleStopRecording = useCallback(async () => {
    const rec = recorderRef.current;
    if (!rec) return;
    recorderRef.current = null;
    // If the user released BEFORE start() finished, treat it as a
    // cancel (rec.stop() would just await the in-flight start). The
    // tap was too short to produce audio anyway.
    if (!rec.isStarted()) {
      rec.cancel();
      setRecording(false);
      setMicLevel(0);
      return;
    }
    setRecording(false);
    setMicLevel(0);
    let captured: RecordedAudio;
    try {
      captured = await rec.stop();
    } catch (err) {
      // MicAborted = the start was cancelled mid-flight; ignore.
      if (err instanceof MicAborted) return;
      const msg = err instanceof Error ? err.message : "recording failed";
      setVoiceError(msg);
      toast.error("Recording failed", { description: msg });
      return;
    }
    // Ignore stray taps that produce near-zero audio.
    if (captured.blob.size < 1024 || captured.durationMs < 300) {
      toast.message("That was very short", {
        description: "Hold the mic button while you speak.",
      });
      return;
    }
    setTranscribing(true);
    try {
      const out = await transcribe(captured.blob);
      const merged = answer.trim()
        ? `${answer.trim()} ${out.text.trim()}`
        : out.text.trim();
      setAnswer(merged);
    } catch (err) {
      const detail =
        err instanceof ApiError ? err.detail : (err as Error).message;
      setVoiceError(detail);
      toast.error("Transcription failed", { description: detail });
    } finally {
      setTranscribing(false);
    }
  }, [answer]);

  const handleCancelRecording = useCallback(() => {
    const rec = recorderRef.current;
    if (!rec) return;
    recorderRef.current = null;
    rec.cancel();
    setRecording(false);
    setMicLevel(0);
  }, []);

  // Spacebar push-to-talk while the interview question is on screen,
  // plus Escape as a last-resort bail-out when the recorder UI is
  // stuck for any reason. Ignored when the user is focused in an
  // input/textarea so typing is unaffected.
  useEffect(() => {
    if (!voiceActive || stage.kind !== "interview") return;
    const isTypingTarget = (t: EventTarget | null) => {
      if (!(t instanceof HTMLElement)) return false;
      const tag = t.tagName.toLowerCase();
      return (
        tag === "input" ||
        tag === "textarea" ||
        t.isContentEditable
      );
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.code === "Escape") {
        // Hard reset: drop any in-flight recording and clear errors.
        if (recorderRef.current) handleCancelRecording();
        setVoiceError(null);
        setPreparing(false);
        return;
      }
      if (e.code !== "Space" || e.repeat) return;
      if (isTypingTarget(e.target)) return;
      e.preventDefault();
      void handleStartRecording();
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      if (isTypingTarget(e.target)) return;
      e.preventDefault();
      if (recorderRef.current) void handleStopRecording();
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    };
  }, [
    voiceActive,
    stage.kind,
    handleStartRecording,
    handleStopRecording,
    handleCancelRecording,
  ]);

  // rAF loop for the level meter while recording.
  useEffect(() => {
    if (!recording) return;
    let raf = 0;
    const tick = () => {
      const lv = recorderRef.current?.level() ?? 0;
      setMicLevel(lv);
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [recording]);

  // Auto-play TTS of each new question. The "key" is the question prompt
  // text itself -- on follow-ups the prompt changes but the index might
  // not advance, so this is the right signal.
  useEffect(() => {
    if (!voiceActive) {
      lastSpokenQuestionRef.current = null;
      return;
    }
    if (stage.kind !== "interview") return;
    const prompt = stage.state.question?.prompt;
    if (!prompt) return;
    if (lastSpokenQuestionRef.current === prompt) return;
    lastSpokenQuestionRef.current = prompt;
    stopTts();
    setSpeaking(true);
    let cancelled = false;
    speak({ text: prompt })
      .then((wav) => {
        if (cancelled) return;
        const handle = playWavBlob(wav);
        playbackRef.current = handle;
        handle.finished.then(() => {
          if (playbackRef.current === handle) {
            playbackRef.current = null;
          }
          setSpeaking(false);
        });
      })
      .catch((err) => {
        setSpeaking(false);
        const detail =
          err instanceof ApiError ? err.detail : (err as Error).message;
        // Soft-fail: TTS is decorative. The user can still read the
        // prompt and answer normally.
        setVoiceError(detail);
        toast.message("TTS unavailable", {
          description:
            detail +
            " — the question is still on screen; you can read it and reply.",
        });
      });
    return () => {
      cancelled = true;
    };
  }, [voiceActive, stage, stopTts]);

  if (stage.kind === "done") {
    return (
      <div className="mx-auto max-w-xl animate-fade-in">
        <Card className="border-success/40 bg-success/5">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="h-5 w-5 text-success" />
              Persona saved
            </CardTitle>
            <CardDescription>
              Spec, transcript, and embeddings are persisted under{" "}
              <span className="font-mono">~/.x-agent/personas</span>.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-md border border-border bg-background/60 p-3 font-mono text-xs">
              {stage.state.persona_id}
            </div>
            <p className="text-sm text-muted-foreground">
              You can refine, evaluate, or draft as this persona now.
            </p>
          </CardContent>
          <CardFooter className="gap-2">
            <Button asChild variant="outline">
              <Link to="/personas">Back to personas</Link>
            </Button>
            <Button asChild>
              <Link to={`/draft?persona=${stage.state.persona_id}`}>
                <Wand2 className="h-4 w-4" />
                Draft as this persona
              </Link>
            </Button>
            <Button
              variant="ghost"
              onClick={() => navigate(`/personas/${stage.state.persona_id}`)}
            >
              View persona
            </Button>
          </CardFooter>
        </Card>
      </div>
    );
  }

  if (stage.kind === "interview") {
    const { state } = stage;
    const q = state.question;
    if (!q) {
      return (
        <Alert variant="warning" className="animate-fade-in">
          <Megaphone className="h-4 w-4" />
          <div>
            <AlertTitle>No question available.</AlertTitle>
            <AlertDescription>
              The server returned no pending question. Try refreshing and
              starting again.
            </AlertDescription>
          </div>
        </Alert>
      );
    }
    const total = state.total || 1;
    const idx = state.question_index;
    const pct = ((idx + 1) / total) * 100;
    const submitDisabled = answerMutation.isPending;
    const micBusy = recording || transcribing || preparing;
    // Ring scale tied to RMS level so the visual is honest. Clamp keeps
    // a quiet voice still readable and a loud one from blowing the card.
    const micScale = 1 + Math.min(0.4, micLevel * 0.6);

    return (
      <div className="mx-auto max-w-3xl space-y-4 animate-fade-in">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">
              Persona interview
            </p>
            <h1 className="text-xl font-semibold tracking-tight">
              Question {idx + 1} of {total}
            </h1>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline">{q.dimension}</Badge>
            {q.kind === "generative" ? (
              <Badge variant="default">writing sample</Badge>
            ) : null}
            {q.is_followup ? <Badge variant="warning">follow-up</Badge> : null}
            {voiceActive ? (
              <Badge variant="default" className="gap-1">
                <AudioLines className="h-3 w-3" />
                voice
              </Badge>
            ) : null}
          </div>
        </div>
        <Progress value={pct} />
        <Card>
          <CardHeader>
            <CardTitle className="flex items-start gap-3 text-base font-medium leading-relaxed">
              <MessageSquare className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
              <span>{q.prompt}</span>
            </CardTitle>
            {voiceActive ? (
              <div className="flex items-center gap-2 pt-1 text-xs text-muted-foreground">
                {speaking ? (
                  <>
                    <Volume2 className="h-3.5 w-3.5 animate-pulse text-primary" />
                    <span>Reading the question…</span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={stopTts}
                      className="h-6 px-2 text-xs"
                    >
                      <Square className="h-3 w-3" />
                      Stop
                    </Button>
                  </>
                ) : (
                  <>
                    <Mic className="h-3.5 w-3.5" />
                    <span>
                      Hold the mic (or press <kbd className="rounded bg-muted px-1">space</kbd>) to record your answer.
                    </span>
                  </>
                )}
              </div>
            ) : null}
          </CardHeader>
          <CardContent className="space-y-4">
            {voiceActive ? (
              <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-background/50 p-4">
                <button
                  type="button"
                  aria-label={
                    recording
                      ? "Stop recording"
                      : preparing
                        ? "Preparing microphone"
                        : "Hold to record"
                  }
                  aria-pressed={recording || preparing}
                  disabled={transcribing || submitDisabled}
                  onPointerDown={(e) => {
                    if (e.button !== 0) return;
                    e.preventDefault();
                    void handleStartRecording();
                  }}
                  onPointerUp={(e) => {
                    e.preventDefault();
                    if (recorderRef.current) void handleStopRecording();
                  }}
                  onPointerLeave={() => {
                    if (recorderRef.current) handleCancelRecording();
                  }}
                  onContextMenu={(e) => e.preventDefault()}
                  className={[
                    "relative inline-flex h-20 w-20 items-center justify-center",
                    "rounded-full border-2 transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    "disabled:opacity-50 disabled:cursor-not-allowed",
                    recording
                      ? "border-destructive bg-destructive/15 text-destructive shadow-[0_0_0_8px_hsl(var(--destructive)/0.18)]"
                      : preparing
                        ? "border-primary/40 bg-primary/10 text-primary"
                        : "border-primary/40 bg-primary/10 text-primary hover:bg-primary/15",
                  ].join(" ")}
                  style={{
                    transform: recording ? `scale(${micScale})` : undefined,
                    transition: "transform 60ms linear, background-color 120ms ease",
                  }}
                >
                  {transcribing || preparing ? (
                    <Loader2 className="h-8 w-8 animate-spin" />
                  ) : (
                    <Mic className="h-8 w-8" />
                  )}
                </button>
                <div className="text-center text-xs text-muted-foreground">
                  {transcribing ? (
                    <span>Transcribing locally with faster-whisper…</span>
                  ) : preparing ? (
                    <span>
                      Opening microphone… release to cancel, or hold to keep
                      recording.
                    </span>
                  ) : recording ? (
                    <span>
                      Recording. Release to transcribe, drag off to cancel.
                    </span>
                  ) : (
                    <span>
                      Hold to talk · double-tap mic to re-record · text edit
                      below before submitting.
                    </span>
                  )}
                </div>
                {voiceError ? (
                  <div className="flex flex-col items-center gap-1">
                    <p className="text-xs text-destructive">{voiceError}</p>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        if (recorderRef.current) handleCancelRecording();
                        setVoiceError(null);
                        setPreparing(false);
                        setRecording(false);
                        setMicLevel(0);
                      }}
                      className="h-6 px-2 text-xs"
                    >
                      Reset mic
                    </Button>
                  </div>
                ) : null}
              </div>
            ) : null}
            <Textarea
              autoSize
              autoFocus={!voiceActive}
              rows={6}
              maxLength={20_000}
              value={answer}
              placeholder={
                voiceActive
                  ? "Your transcribed answer will appear here — edit if needed, then Submit."
                  : q.kind === "generative"
                    ? "Write a short post in your real voice (2-4 sentences)…"
                    : "Type your answer. Be specific. Skip if you'd rather move on."
              }
              onChange={(e) => setAnswer(e.target.value)}
              disabled={submitDisabled || transcribing}
            />
          </CardContent>
          <CardFooter className="flex flex-wrap items-center justify-between gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={handleSaveAndExit}
              disabled={submitDisabled}
            >
              <Save className="h-4 w-4" />
              Save &amp; continue later
            </Button>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="ghost"
                onClick={() =>
                  answerMutation.mutate({
                    threadId: state.thread_id,
                    answer: "",
                  })
                }
                disabled={submitDisabled || micBusy}
              >
                <SkipForward className="h-4 w-4" />
                Skip
              </Button>
              <Button
                loading={answerMutation.isPending}
                disabled={!answer.trim() || submitDisabled || micBusy}
                onClick={() =>
                  submitTranscript(state.thread_id, answer)
                }
              >
                Submit answer
              </Button>
            </div>
          </CardFooter>
        </Card>
        <p className="text-center text-xs text-muted-foreground">
          {voiceActive ? (
            <>
              Audio never touches disk: it's transcribed in-memory on your
              local server, then discarded. Only the resulting text is
              appended to <span className="font-mono">transcript.jsonl</span>.
            </>
          ) : (
            <>
              Your answers are mirrored to <span className="font-mono">localStorage</span>{" "}
              and appended to <span className="font-mono">~/.x-agent/personas/&lt;id&gt;/transcript.jsonl</span>
              {" "}as you go. Refreshing this tab will resume the wizard.
            </>
          )}
        </p>
      </div>
    );
  }

  // Stage: consent
  const submitDisabled =
    !form.name.trim() ||
    (form.isReal && (!form.consentAck || !form.disclosure.trim())) ||
    startMutation.isPending;

  return (
    <div className="mx-auto max-w-2xl space-y-4 animate-fade-in">
      <div>
        <p className="text-xs uppercase tracking-wide text-muted-foreground">
          New persona
        </p>
        <h1 className="text-2xl font-semibold tracking-tight">
          Capture a voice via conversation
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {voiceModeAvailable && form.voiceMode
            ? "Talk through the interview — Kokoro reads each question aloud and faster-whisper transcribes your answers, all on your machine."
            : "The agent walks you through 6–40 questions and extracts a structured persona spec from your answers."}
        </p>
      </div>

      {showResumePrompt && persisted ? (
        <Alert className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-start gap-3">
            <CornerUpLeft className="mt-0.5 h-4 w-4" />
            <div>
              <AlertTitle>Resume your in-progress interview?</AlertTitle>
              <AlertDescription>
                Saved {new Date(persisted.savedAt).toLocaleString()} for{" "}
                <span className="font-medium">{persisted.form.name || "untitled"}</span>{" "}
                — question {persisted.state.question_index + 1} of{" "}
                {persisted.state.total}.
              </AlertDescription>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={handleDiscardSaved}>
              <X className="h-4 w-4" />
              Discard
            </Button>
            <Button size="sm" onClick={handleResume}>
              <CornerUpLeft className="h-4 w-4" />
              Resume
            </Button>
          </div>
        </Alert>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-5 w-5 text-primary" />
            Subject details
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="name">Display name</Label>
            <Input
              id="name"
              value={form.name}
              maxLength={120}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="e.g. Abhi"
              autoFocus
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex items-end justify-between gap-3 rounded-md border border-border p-3">
              <div className="space-y-1">
                <Label htmlFor="real" className="cursor-pointer">
                  Real person
                </Label>
                <p className="text-xs text-muted-foreground">
                  Adds consent + auto-disclosure to every post.
                </p>
              </div>
              <Switch
                id="real"
                checked={form.isReal}
                onCheckedChange={(v) => setForm({ ...form, isReal: v })}
              />
            </div>
            <div className="flex items-end justify-between gap-3 rounded-md border border-border p-3">
              <div className="space-y-1">
                <Label htmlFor="voiceMode" className="cursor-pointer flex items-center gap-1">
                  {voiceModeAvailable ? (
                    <Mic className="h-4 w-4 text-primary" />
                  ) : (
                    <MicOff className="h-4 w-4 text-muted-foreground" />
                  )}
                  Voice mode
                </Label>
                <p className="text-xs text-muted-foreground">
                  {voiceModeAvailable
                    ? "TTS reads questions; push-to-talk for answers."
                    : !micSupportRef.current.ok
                      ? "Browser can't record audio."
                      : !voiceServerKnown
                        ? "Checking server…"
                        : "Voice engines not ready on server."}
                </p>
              </div>
              <Switch
                id="voiceMode"
                disabled={!voiceModeAvailable}
                checked={form.voiceMode && voiceModeAvailable}
                onCheckedChange={(v) =>
                  setForm({ ...form, voiceMode: v })
                }
              />
            </div>
          </div>

          <div className="space-y-2 rounded-md border border-border p-3">
            <Label className="text-sm font-medium">Interview length</Label>
            <div className="grid gap-2 sm:grid-cols-3">
              {(
                [
                  {
                    id: "quick" as const,
                    title: "Quick",
                    body: "~6 questions. First pass; refine later.",
                  },
                  {
                    id: "default" as const,
                    title: "Default",
                    body: "~26 questions. Standard bank.",
                  },
                  {
                    id: "deep" as const,
                    title: "Deep",
                    body: "~40 Qs + adaptive follow-ups. Best with voice.",
                  },
                ] as const
              ).map((m) => {
                const active = form.mode === m.id;
                const recommend =
                  m.id === "deep" && form.voiceMode && voiceModeAvailable;
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setForm({ ...form, mode: m.id })}
                    aria-pressed={active}
                    className={[
                      "text-left rounded-md border p-2 transition-colors",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      active
                        ? "border-primary bg-primary/10"
                        : "border-border hover:border-primary/40 hover:bg-primary/5",
                    ].join(" ")}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium">{m.title}</span>
                      {recommend ? (
                        <Badge variant="default" className="text-[10px]">
                          recommended
                        </Badge>
                      ) : null}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {m.body}
                    </p>
                  </button>
                );
              })}
            </div>
          </div>

          {form.isReal ? (
            <div className="space-y-4 rounded-md border border-warning/30 bg-warning/5 p-4">
              <div className="flex items-start gap-3">
                <Megaphone className="mt-0.5 h-4 w-4 text-warning" />
                <p className="text-sm text-warning-foreground/90">
                  By continuing you confirm the subject has agreed to
                  participate, and that any generated posts will carry an
                  AI-persona disclosure.
                </p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="handle">X handle (optional)</Label>
                  <Input
                    id="handle"
                    value={form.handle}
                    placeholder="abhi"
                    maxLength={60}
                    onChange={(e) =>
                      setForm({ ...form, handle: e.target.value })
                    }
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="disclosure">Disclosure tag</Label>
                  <Input
                    id="disclosure"
                    value={form.disclosure}
                    placeholder="[AI persona of @handle]"
                    maxLength={120}
                    onChange={(e) =>
                      setForm({ ...form, disclosure: e.target.value })
                    }
                  />
                </div>
              </div>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={form.consentAck}
                  onChange={(e) =>
                    setForm({ ...form, consentAck: e.target.checked })
                  }
                  className="h-4 w-4 rounded border-border bg-background accent-primary"
                />
                I have the subject's explicit consent.
              </label>
            </div>
          ) : null}
        </CardContent>
        <CardFooter className="justify-end gap-2">
          <Button asChild variant="ghost">
            <Link to="/personas">Cancel</Link>
          </Button>
          <Button
            loading={startMutation.isPending}
            disabled={submitDisabled}
            onClick={() => startMutation.mutate()}
          >
            <Sparkles className="h-4 w-4" />
            Start interview
          </Button>
        </CardFooter>
      </Card>
    </div>
  );
}
