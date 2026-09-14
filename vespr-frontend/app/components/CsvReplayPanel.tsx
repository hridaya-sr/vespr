"use client";

import { useEffect, useRef, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
// Base playback tick at 1x, matching the live demo's 10Hz telemetry cadence —
// not the CSV's own recorded sample rate, just a steady replay pace. Actual
// per-tick delay is this divided by the current speed multiplier.
const PLAYBACK_INTERVAL_MS = 100;
const SPEED_OPTIONS = [0.25, 1, 2, 5, 10, 20] as const;

// Spacing scale: every padding/margin/gap utility in this file is one of
// 4/8/16/24/32px (Tailwind's 1/2/4/6/8). Radius/shadow are shared through
// these two card styles so every panel reads as the same visual language;
// the single sky accent marks interactive/primary elements (buttons, the
// EKF line) against the neutral (black/neutral-900/950) base palette.
const CARD_OUTER = "bg-neutral-900 rounded-lg border border-neutral-800 shadow-md";
const CARD_INNER = "bg-neutral-950 rounded-lg border border-neutral-800 shadow-md";
const BTN_PRIMARY =
  "px-4 py-2 rounded-md bg-sky-600 hover:bg-sky-500 disabled:bg-neutral-700 disabled:cursor-not-allowed text-sm";
const BTN_SECONDARY = "px-4 py-2 rounded-md bg-neutral-800 hover:bg-neutral-700 text-sm";
const SELECT_STYLE = "bg-neutral-800 border border-neutral-700 rounded-md px-2 py-1 text-sm";

// Shared chart-axis styling: axis text always stays a neutral gray token,
// never the series color — the line carries identity, the ticks don't.
// Gridlines are solid hairlines, not dashed, so they stay recessive.
const AXIS_STROKE = "#555";
const AXIS_TICK = { fill: "#888", fontSize: 11 };
const GRID_STROKE = "#262626";
const TOOLTIP_STYLE = { backgroundColor: "#1a1a1a", border: "1px solid #333", fontSize: 12 };
const CHART_MARGIN = { top: 8, right: 16, left: 4, bottom: 4 };
const formatTime = (v: number) => v.toFixed(1);
const formatWhole = (v: number) => Math.round(v).toString();
const formatBias = (v: number) => v.toFixed(3);

type FieldKey = "timestamp" | "altitude" | "velocity" | "acceleration";

const REQUIRED_FIELDS: FieldKey[] = ["timestamp", "altitude"];
const OPTIONAL_FIELDS: FieldKey[] = ["velocity", "acceleration"];

const FIELD_LABELS: Record<FieldKey, string> = {
  timestamp: "Timestamp",
  altitude: "Altitude",
  velocity: "Velocity (optional)",
  acceleration: "Acceleration (optional)",
};

// Shown as the first <option> in an optional field's dropdown, in place of
// a real column — selecting it leaves that field unmapped.
const OPTIONAL_FIELD_PLACEHOLDER: Record<string, string> = {
  velocity: "No velocity column available",
  acceleration: "No acceleration channel available, derive from altitude/velocity instead",
};

interface UploadResponse {
  session_id: string;
  // Non-numeric columns (GPS coordinates with a degree symbol, free-text
  // status columns, etc) are already dropped server-side by upload time,
  // so every column here is safe to offer in the mapping dropdowns.
  columns: string[];
  preview_rows: Record<string, string>[];
  suggested_mapping: Record<FieldKey, string | null>;
}

interface NormalizedRow {
  timestamp: number;
  altitude: number;
  acceleration: number;
}

interface ConfirmResponse {
  session_id: string;
  row_count: number;
  rows: NormalizedRow[];
}

interface StepFrame {
  index: number;
  timestamp: number;
  altitude: number;
  acceleration: number;
  altitude_filtered_m: number;
  velocity_filtered_ms: number;
  accel_bias_est: number;
  // Measurement minus EKF-predicted altitude, and whether it exceeds a
  // 3-sigma gate against its own covariance — see ekf.py's update() and
  // csv_sessions.py's step(). A large innovation means the barometer
  // reading disagreed with the filter's model more than expected: either
  // an outlier sample or a real event the model didn't anticipate.
  innovation_m: number;
  is_anomaly: boolean;
}

type Phase = "boost" | "coast" | "descent";

const PHASE_COLORS: Record<Phase, string> = {
  boost: "bg-orange-600",
  coast: "bg-blue-600",
  descent: "bg-purple-600",
};

// CSV rows carry no phase label, so it's derived from the same physics
// simulation.py's flight_physics() uses: positive net acceleration means
// the vehicle is still under thrust (boost); once unpowered, acceleration
// settles near -g and the sign of EKF velocity tells ascent from descent.
// This assumes "acceleration" is gravity-compensated net accel, matching
// the convention the EKF's predict() step already assumes for a_meas.
function derivePhase(frame: StepFrame): Phase {
  if (frame.acceleration > 0) return "boost";
  return frame.velocity_filtered_ms > 0 ? "coast" : "descent";
}

type Stage = "idle" | "uploading" | "mapping" | "confirmed";

// Separate from `stage` (which drives the upload/mapping wizard): this is
// the playback state machine from the spec. It has no "mapping" value of
// its own — it stays "idle"/"uploading" in lockstep with `stage` until the
// mapping is confirmed, at which point `stage` freezes at "confirmed" and
// `status` takes over as the source of truth for the Play/Replay button.
type PlaybackStatus = "idle" | "uploading" | "ready" | "playing" | "paused" | "ended";

export default function CsvReplayPanel() {
  const [stage, setStage] = useState<Stage>("idle");
  const [status, setStatus] = useState<PlaybackStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  // Backends on a cold-start-prone host (e.g. a free Render instance) can
  // take up to a minute to answer their first request. Ping the health
  // endpoint on load and gate the upload button on it, rather than letting
  // someone's first upload silently hang against a still-waking backend.
  const [backendReady, setBackendReady] = useState(false);
  const backendCheckTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function pingBackend() {
      try {
        const res = await fetch(`${API_URL}/`);
        if (!cancelled && res.ok) {
          setBackendReady(true);
          return;
        }
      } catch {
        // backend not reachable yet — fall through to retry below
      }
      if (!cancelled) {
        backendCheckTimer.current = setTimeout(pingBackend, 3000);
      }
    }

    pingBackend();

    return () => {
      cancelled = true;
      if (backendCheckTimer.current) clearTimeout(backendCheckTimer.current);
    };
  }, []);

  const [upload, setUpload] = useState<UploadResponse | null>(null);
  const [mapping, setMapping] = useState<Record<FieldKey, string>>({
    timestamp: "",
    altitude: "",
    velocity: "",
    acceleration: "",
  });
  const [altitudeUnit, setAltitudeUnit] = useState<"m" | "ft">("m");
  const [timestampUnit, setTimestampUnit] = useState<"s" | "ms">("s");
  const [confirmed, setConfirmed] = useState<ConfirmResponse | null>(null);
  const [currentFrame, setCurrentFrame] = useState<StepFrame | null>(null);
  const [playbackFrames, setPlaybackFrames] = useState<StepFrame[]>([]);
  const [speed, setSpeed] = useState<number>(1);
  // playFrame recurses through chained setTimeout closures, each fixed at
  // whatever `speed` was in scope when that particular closure was created
  // — a plain state read inside it would keep using the speed from when
  // playback *started*, not the latest one, if changed mid-playback. A ref
  // is never captured by value, so reading speedRef.current always sees
  // the current speed even from an older closure.
  const speedRef = useRef(1);
  const playbackTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Bumped on every reset/new-upload. Stopping the timer only prevents
  // *future* ticks — a fetch already in flight at that instant still
  // resolves later and would otherwise stomp on newer state (e.g. an old
  // file's stray frame landing after a new file's confirm). Each in-flight
  // request captures the generation it started with and checks it's still
  // current before applying any state update.
  const playbackGeneration = useRef(0);
  // The index of the next frame to play — kept up to date after every
  // successful step, so Play (after a Pause) resumes exactly where it left
  // off instead of restarting from 0.
  const nextIndexRef = useRef(0);
  // Whether playback *should* currently be advancing. Checked right after
  // an in-flight /csv/step response comes back, so a Pause that lands while
  // a request is already in flight stops after that one frame rather than
  // scheduling another (stopping the timer alone only prevents *future*
  // ticks, not one already underway).
  const playingRef = useRef(false);
  // True while a manual step-forward/backward/seek request is in flight —
  // disables the step buttons so rapid clicks can't fire overlapping
  // requests against the same shared session EKF. Mirrored into a ref
  // (isSteppingRef) for synchronous checks inside rapid-fire drag-event
  // handlers, where reading the state value directly would see a stale
  // pre-update snapshot until the next render.
  const [isStepping, setIsStepping] = useState(false);
  const isSteppingRef = useRef(false);
  function setStepping(value: boolean) {
    isSteppingRef.current = value;
    setIsStepping(value);
  }
  // Bumped on every manual step/seek call; each request checks it's still
  // the latest before applying its result, so an earlier (slower) request
  // can't resolve after a later (faster) one and overwrite it.
  const manualStepId = useRef(0);
  // Non-null only while actively dragging the scrubber — holds the
  // in-progress drag position so the thumb/label track the pointer
  // immediately, without firing a seek on every intermediate value.
  const [scrubIndex, setScrubIndex] = useState<number | null>(null);
  // The most recently requested scrub position that hasn't been chased
  // (seeked to) yet. Dragging can generate onChange events far faster than
  // the backend can answer them; rather than firing (and half-abandoning)
  // a seek per event, each drag event just updates this and the chart
  // chases it — one seek in flight at a time, always for the latest
  // position, so the chart visibly advances through every seek along the
  // way instead of jumping once at the end.
  const pendingScrubTarget = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (playbackTimer.current) clearTimeout(playbackTimer.current);
    };
  }, []);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;

    // A previous file's playback loop keeps ticking in the background
    // (recursive setTimeout) until it either finishes or is stopped — it
    // isn't tied to which file is currently displayed. Without this, its
    // next in-flight /csv/step call lands after the new file is confirmed,
    // using the old (now-gone) session_id: that 404s into an "Unknown or
    // expired CSV session" error, while a stray response from just before
    // it can still repopulate currentFrame/playbackFrames with the old
    // file's data — producing exactly the contradictory "error banner +
    // success message + stale chart" combination this was caught from.
    stopPlaybackTimer();
    playbackGeneration.current += 1;

    setError(null);
    setConfirmed(null);
    setCurrentFrame(null);
    setPlaybackFrames([]);
    setStage("uploading");
    setStatus("uploading");

    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch(`${API_URL}/csv/upload`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Upload failed (${res.status})`);
      }
      const data: UploadResponse = await res.json();
      setUpload(data);
      setMapping({
        timestamp: data.suggested_mapping.timestamp ?? data.columns[0] ?? "",
        altitude: data.suggested_mapping.altitude ?? data.columns[0] ?? "",
        // Optional fields default to unmapped (not the first column) when no
        // confident guess exists — an accidental guess here would silently
        // fabricate a signal from an unrelated column.
        velocity: data.suggested_mapping.velocity ?? "",
        acceleration: data.suggested_mapping.acceleration ?? "",
      });
      setStage("mapping");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
      setStage("idle");
      setStatus("idle");
    } finally {
      e.target.value = "";
    }
  }

  // Only non-empty (i.e. actually mapped) selections count toward the
  // duplicate check — two optional fields both left unmapped ("") aren't
  // a collision.
  const mappedColumns = Object.values(mapping).filter((v) => v !== "");
  const hasDuplicateMapping = new Set(mappedColumns).size !== mappedColumns.length;

  async function handleConfirm() {
    if (!upload || hasDuplicateMapping) return;
    setError(null);

    try {
      const res = await fetch(`${API_URL}/csv/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: upload.session_id,
          mapping: {
            timestamp: mapping.timestamp,
            altitude: mapping.altitude,
            velocity: mapping.velocity || null,
            acceleration: mapping.acceleration || null,
          },
          units: { altitude_unit: altitudeUnit, timestamp_unit: timestampUnit },
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Normalization failed (${res.status})`);
      }
      const data: ConfirmResponse = await res.json();
      setConfirmed(data);
      setStage("confirmed");
      nextIndexRef.current = 0;
      playingRef.current = false;
      setStatus("ready");
      setCurrentFrame(null);
      setPlaybackFrames([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Normalization failed");
    }
  }

  function stopPlaybackTimer() {
    if (playbackTimer.current) {
      clearTimeout(playbackTimer.current);
      playbackTimer.current = null;
    }
  }

  function reset() {
    stopPlaybackTimer();
    playbackGeneration.current += 1;
    playingRef.current = false;
    nextIndexRef.current = 0;
    setStage("idle");
    setStatus("idle");
    setUpload(null);
    setConfirmed(null);
    setCurrentFrame(null);
    setPlaybackFrames([]);
    setError(null);
  }

  // Recursive setTimeout rather than a raw setInterval: each tick waits for
  // the previous /csv/step call to land before scheduling the next one, so
  // requests can never arrive out of order at the session's shared EKF —
  // which a fixed-rate setInterval couldn't guarantee if a request stalls.
  function playFrame(sessionId: string, index: number, totalRows: number, generation: number) {
    playbackTimer.current = setTimeout(async () => {
      try {
        const res = await fetch(`${API_URL}/csv/step`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, index }),
        });

        // A reset or new upload happened while this request was in flight —
        // it belongs to a session that's no longer current, so drop it
        // rather than let it overwrite newer state (or a newer session's
        // own in-flight request).
        if (generation !== playbackGeneration.current) return;

        if (!res.ok) {
          const body = await res.json().catch(() => null);
          throw new Error(body?.detail || `Playback failed (${res.status})`);
        }
        const frame: StepFrame = await res.json();
        setCurrentFrame(frame);
        setPlaybackFrames((prev) => [...prev, frame]);
        nextIndexRef.current = index + 1;

        if (index + 1 >= totalRows) {
          playbackTimer.current = null;
          playingRef.current = false;
          setStatus("ended");
        } else if (playingRef.current) {
          playFrame(sessionId, index + 1, totalRows, generation);
        } else {
          // Paused while this request was in flight — this frame still
          // applies (it was already committed to happening), but stop
          // here instead of scheduling the next one.
          playbackTimer.current = null;
        }
      } catch (err) {
        if (generation !== playbackGeneration.current) return;
        playbackTimer.current = null;
        playingRef.current = false;
        setError(err instanceof Error ? err.message : "Playback failed");
        setStatus("ready");
      }
      // Read at call time, not once when the loop started, so a speed
      // change mid-playback takes effect from the very next scheduled
      // tick rather than only after Pause/Play. (A pending, already-
      // scheduled tick still fires at its original delay — only the tick
      // scheduled *after* that one uses the new speed.)
    }, PLAYBACK_INTERVAL_MS / speedRef.current);
  }

  function handlePlay() {
    // Guard against a still-in-flight scrub/step (isSteppingRef) exactly
    // like every other action handler here does. Without this, clicking
    // Play right after an aggressive scrub — before chaseScrubTarget's
    // last seekTo() has resolved and finished updating nextIndexRef — starts
    // a second, independent playFrame loop on top of the still-running
    // seek chase. Both then race over the same shared backend EKF (one
    // calling /csv/step, the other /csv/seek), corrupting the frame order
    // and EKF state — which shows up as playback looking stuck at a much
    // slower effective pace than whatever speed was actually selected.
    if (!confirmed || isSteppingRef.current) return;
    setError(null);
    playingRef.current = true;
    setStatus("playing");
    playFrame(confirmed.session_id, nextIndexRef.current, confirmed.row_count, playbackGeneration.current);
  }

  function handlePause() {
    playingRef.current = false;
    stopPlaybackTimer();
    setStatus("paused");
  }

  // Clicking step forward/backward while the auto-loop is running takes
  // control away from it rather than racing it — both would otherwise read
  // and write nextIndexRef concurrently, and the backend EKF would end up
  // stepped twice for the same row.
  function stopAutoplayForManualStep() {
    if (playingRef.current) {
      playingRef.current = false;
      stopPlaybackTimer();
    }
  }

  async function handleStepForward() {
    if (!confirmed || isSteppingRef.current) return;
    stopAutoplayForManualStep();
    const index = nextIndexRef.current;
    if (index >= confirmed.row_count) return;
    setError(null);
    setStepping(true);
    const requestId = ++manualStepId.current;
    try {
      const res = await fetch(`${API_URL}/csv/step`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: confirmed.session_id, index }),
      });
      if (requestId !== manualStepId.current) return;
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Step failed (${res.status})`);
      }
      const frame: StepFrame = await res.json();
      if (requestId !== manualStepId.current) return;
      setCurrentFrame(frame);
      setPlaybackFrames((prev) => [...prev, frame]);
      nextIndexRef.current = index + 1;
      setStatus(index + 1 >= confirmed.row_count ? "ended" : "paused");
    } catch (err) {
      if (requestId === manualStepId.current) {
        setError(err instanceof Error ? err.message : "Step failed");
      }
    } finally {
      if (requestId === manualStepId.current) setStepping(false);
    }
  }

  // Shared by step-backward and the scrubber: the EKF can't be run in
  // reverse, so reaching an earlier (or arbitrary) frame means resetting to
  // a fresh filter and replaying from row 0 up to the target (see
  // csv_sessions.seek) — confirmed fast even at the far end of a 15k-row
  // file (~200ms server-side). Guarded against out-of-order responses via
  // manualStepId, which matters more here than for a single step: dragging
  // the scrubber can fire several of these in a row, and a slower earlier
  // request could otherwise resolve after a faster later one and stomp on
  // its result. Uses the isSteppingRef (not the isStepping state) for its
  // own re-entrancy guard, since the scrubber's chase loop calls this
  // repeatedly in quick succession — reading state there would see a stale
  // value until the next render, letting more than one call through.
  async function seekTo(targetIndex: number) {
    if (!confirmed || isSteppingRef.current) return;
    stopAutoplayForManualStep();
    if (targetIndex < 0 || targetIndex >= confirmed.row_count) return;
    setError(null);
    setStepping(true);
    const requestId = ++manualStepId.current;
    try {
      const res = await fetch(`${API_URL}/csv/seek`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: confirmed.session_id, index: targetIndex }),
      });
      if (requestId !== manualStepId.current) return;
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Seek failed (${res.status})`);
      }
      // The backend returns every frame from 0 through targetIndex (not
      // just the last one) so the chart shows a continuous trace even when
      // jumping into territory that was never actually played through.
      const frames: StepFrame[] = await res.json();
      if (requestId !== manualStepId.current) return;
      setPlaybackFrames(frames);
      setCurrentFrame(frames[frames.length - 1] ?? null);
      nextIndexRef.current = targetIndex + 1;
      setStatus(targetIndex + 1 >= confirmed.row_count ? "ended" : "paused");
    } catch (err) {
      if (requestId === manualStepId.current) {
        setError(err instanceof Error ? err.message : "Seek failed");
      }
    } finally {
      if (requestId === manualStepId.current) setStepping(false);
    }
  }

  function handleStepBackward() {
    seekTo(nextIndexRef.current - 2);
  }

  // Chases pendingScrubTarget one seek at a time: only ever one request in
  // flight, always for the most recently requested position — so a fast
  // drag doesn't flood the backend with (mostly wasted) requests, but the
  // chart still visibly advances through each completed seek along the way
  // rather than jumping once at the very end.
  function chaseScrubTarget() {
    if (isSteppingRef.current) return;
    const target = pendingScrubTarget.current;
    if (target === null) return;
    pendingScrubTarget.current = null;
    seekTo(target).then(() => {
      if (pendingScrubTarget.current !== null) {
        chaseScrubTarget();
      } else {
        // Fully caught up with the drag — let the slider reflect
        // currentFrame again instead of the (now-stale) local override.
        setScrubIndex(null);
      }
    });
  }

  function handleScrubChange(e: React.ChangeEvent<HTMLInputElement>) {
    const value = Number(e.target.value);
    setScrubIndex(value);
    pendingScrubTarget.current = value;
    chaseScrubTarget();
  }

  function handleScrubCommit(e: React.SyntheticEvent<HTMLInputElement>) {
    // A safety net, not the primary trigger — handleScrubChange already
    // queues and chases every value as it changes, release included. This
    // just makes sure the released value is definitely the one chased
    // (e.g. a keyboard nudge that fires onKeyUp without a drag in between).
    const target = Number((e.target as HTMLInputElement).value);
    pendingScrubTarget.current = target;
    chaseScrubTarget();
  }

  async function handleReplay() {
    if (!confirmed) return;
    setError(null);
    try {
      const res = await fetch(`${API_URL}/csv/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: confirmed.session_id }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.detail || `Reset failed (${res.status})`);
      }
      setCurrentFrame(null);
      setPlaybackFrames([]);
      nextIndexRef.current = 0;
      playingRef.current = false;
      setStatus("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
    }
  }

  return (
    <div className={`w-full ${CARD_OUTER} p-4 mb-6`}>
      <h2 className="text-sm font-semibold text-neutral-200 mb-4">
        CSV Flight Log Replay
      </h2>

      {(stage === "idle" || stage === "uploading") && (
        <div className="border-2 border-dashed border-neutral-700 rounded-lg p-8 flex flex-col items-center gap-4 text-center">
          <UploadIcon />
          {stage === "uploading" ? (
            <p className="text-sm text-neutral-400">Uploading…</p>
          ) : !backendReady ? (
            <p className="text-sm text-neutral-400">
              Connecting to backend, first load can take up to a minute…
            </p>
          ) : (
            <>
              <div>
                <p className="text-neutral-300 font-medium">Upload a flight log to begin</p>
                <p className="text-neutral-500 text-sm mt-1">
                  CSV with timestamp and altitude columns (velocity and acceleration optional)
                </p>
              </div>
              <label className={`${BTN_PRIMARY} cursor-pointer`}>
                Upload CSV
                <input
                  type="file"
                  accept=".csv,text/csv"
                  className="hidden"
                  onChange={handleFileChange}
                />
              </label>
            </>
          )}
        </div>
      )}

      {error && <p className="text-sm text-red-400 mt-4">{error}</p>}

      {stage === "mapping" && upload && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            {REQUIRED_FIELDS.map((field) => (
              <div key={field}>
                <label className="block text-xs text-neutral-400 uppercase mb-1">
                  {FIELD_LABELS[field]}
                </label>
                <select
                  className={`w-full ${SELECT_STYLE}`}
                  value={mapping[field]}
                  onChange={(e) =>
                    setMapping((m) => ({ ...m, [field]: e.target.value }))
                  }
                >
                  {upload.columns.map((col) => (
                    <option key={col} value={col}>
                      {col}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>

          <div>
            <p className="text-xs text-neutral-500 uppercase mb-2">
              Optional signals
            </p>
            <div className="grid grid-cols-2 gap-4">
              {OPTIONAL_FIELDS.map((field) => (
                <div key={field}>
                  <label className="block text-xs text-neutral-400 uppercase mb-1">
                    {FIELD_LABELS[field]}
                  </label>
                  <select
                    className={`w-full ${SELECT_STYLE}`}
                    value={mapping[field]}
                    onChange={(e) =>
                      setMapping((m) => ({ ...m, [field]: e.target.value }))
                    }
                  >
                    <option value="">{OPTIONAL_FIELD_PLACEHOLDER[field]}</option>
                    {upload.columns.map((col) => (
                      <option key={col} value={col}>
                        {col}
                      </option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          </div>

          <div className="flex gap-6">
            <div>
              <label className="block text-xs text-neutral-400 uppercase mb-1">
                Altitude unit
              </label>
              <select
                className={SELECT_STYLE}
                value={altitudeUnit}
                onChange={(e) => setAltitudeUnit(e.target.value as "m" | "ft")}
              >
                <option value="m">Meters</option>
                <option value="ft">Feet</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-neutral-400 uppercase mb-1">
                Timestamp unit
              </label>
              <select
                className={SELECT_STYLE}
                value={timestampUnit}
                onChange={(e) => setTimestampUnit(e.target.value as "s" | "ms")}
              >
                <option value="s">Seconds</option>
                <option value="ms">Milliseconds</option>
              </select>
            </div>
          </div>

          {hasDuplicateMapping && (
            <p className="text-sm text-red-400">
              Each mapped field must use a different column.
            </p>
          )}

          <div className="text-xs text-neutral-500">
            Preview (first {upload.preview_rows.length} rows from the file):
            <pre className={`mt-1 ${CARD_INNER} p-2 overflow-x-auto`}>
              {JSON.stringify(upload.preview_rows, null, 2)}
            </pre>
          </div>

          <div className="flex gap-2">
            <button
              onClick={handleConfirm}
              disabled={hasDuplicateMapping}
              className={BTN_PRIMARY}
            >
              Confirm mapping
            </button>
            <button onClick={reset} className={BTN_SECONDARY}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {stage === "confirmed" && confirmed && (
        <div className="text-sm text-neutral-300 space-y-4">
          <p>
            Normalized and stored {confirmed.row_count} rows for session{" "}
            <span className="font-mono text-neutral-400">
              {confirmed.session_id.slice(0, 8)}
            </span>
            .
          </p>

          <div className="flex items-center gap-4">
            <button
              onClick={handleStepBackward}
              disabled={isStepping || !currentFrame || currentFrame.index <= 0}
              className={`${BTN_SECONDARY} disabled:opacity-40 disabled:cursor-not-allowed`}
              title="Back 1 frame"
            >
              ◀
            </button>

            {(status === "ready" || status === "paused") && (
              <button
                onClick={handlePlay}
                disabled={isStepping}
                className={`${BTN_PRIMARY} disabled:opacity-40 disabled:cursor-not-allowed`}
              >
                Play
              </button>
            )}
            {status === "playing" && (
              <button onClick={handlePause} className={BTN_PRIMARY}>
                Pause
              </button>
            )}
            {status === "ended" && (
              <button onClick={handleReplay} className={BTN_PRIMARY}>
                Replay
              </button>
            )}

            <button
              onClick={handleStepForward}
              disabled={isStepping || (!!currentFrame && currentFrame.index + 1 >= confirmed.row_count)}
              className={`${BTN_SECONDARY} disabled:opacity-40 disabled:cursor-not-allowed`}
              title="Forward 1 frame"
            >
              ▶
            </button>

            <div className="flex items-center gap-1">
              {SPEED_OPTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => {
                    speedRef.current = s;
                    setSpeed(s);
                  }}
                  className={`px-2 py-1 rounded-md text-xs ${
                    speed === s
                      ? "bg-sky-600"
                      : "bg-neutral-800 hover:bg-neutral-700 text-neutral-300"
                  }`}
                >
                  {s}x
                </button>
              ))}
            </div>

            <span className="text-xs text-neutral-500 uppercase tracking-wide">
              Status: {status}
            </span>
            {currentFrame && (
              <span
                className={`text-xs px-2 py-1 rounded-md uppercase ${PHASE_COLORS[derivePhase(currentFrame)]}`}
              >
                {derivePhase(currentFrame)}
              </span>
            )}
            {currentFrame?.is_anomaly && (
              <span className="text-xs px-2 py-1 rounded-md uppercase bg-red-600 animate-pulse">
                Anomaly
              </span>
            )}
            {status !== "playing" && (
              <button onClick={reset} className={`ml-auto ${BTN_SECONDARY}`}>
                Upload a different file
              </button>
            )}
          </div>

          {confirmed.row_count > 1 && (
            <div className="flex items-center gap-4">
              <input
                type="range"
                min={0}
                max={confirmed.row_count - 1}
                value={scrubIndex !== null ? scrubIndex : (currentFrame?.index ?? 0)}
                onChange={handleScrubChange}
                onMouseUp={handleScrubCommit}
                onTouchEnd={handleScrubCommit}
                onKeyUp={handleScrubCommit}
                className="flex-1 accent-sky-600"
              />
              <span className="text-xs text-neutral-500 font-mono w-28 text-right shrink-0">
                {(scrubIndex !== null ? scrubIndex : (currentFrame?.index ?? 0)) + 1} / {confirmed.row_count}
              </span>
            </div>
          )}

          {currentFrame && (
            <div className="grid grid-cols-5 gap-4">
              <MiniStat
                label="Frame"
                value={`${currentFrame.index + 1} / ${confirmed.row_count}`}
              />
              <MiniStat
                label="Raw altitude (m)"
                value={currentFrame.altitude.toFixed(2)}
              />
              <MiniStat
                label="EKF altitude (m)"
                value={currentFrame.altitude_filtered_m.toFixed(2)}
                accent="text-sky-400"
              />
              <MiniStat
                label="EKF velocity (m/s)"
                value={currentFrame.velocity_filtered_ms.toFixed(2)}
                accent="text-pink-400"
              />
              <MiniStat
                label="Innovation (m)"
                value={currentFrame.innovation_m.toFixed(2)}
                accent={currentFrame.is_anomaly ? "text-red-400" : "text-teal-400"}
              />
            </div>
          )}

          {playbackFrames.length > 0 && (
            <>
              <div className={`w-full h-[24rem] ${CARD_INNER} p-4`}>
                <h3 className="text-xs font-semibold text-neutral-400 mb-2 px-1">
                  Altitude (m) — Raw vs. EKF Fused Estimate
                </h3>
                <ResponsiveContainer width="100%" height="88%">
                  <LineChart data={playbackFrames} margin={{ ...CHART_MARGIN, bottom: 16 }}>
                    <CartesianGrid stroke={GRID_STROKE} />
                    <XAxis
                      dataKey="timestamp"
                      type="number"
                      domain={["dataMin", "dataMax"]}
                      tickFormatter={formatTime}
                      stroke={AXIS_STROKE}
                      tick={AXIS_TICK}
                      label={{ value: "Time (s)", position: "bottom", offset: 0, fill: "#888", fontSize: 11 }}
                    />
                    <YAxis
                      stroke={AXIS_STROKE}
                      tick={AXIS_TICK}
                      tickFormatter={formatWhole}
                      width={44}
                    />
                    <Tooltip
                      contentStyle={TOOLTIP_STYLE}
                      labelFormatter={(v) => `t = ${formatTime(Number(v))}s`}
                    />
                    <Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 11 }} />
                    <Line
                      type="monotone"
                      dataKey="altitude"
                      stroke="#525252"
                      strokeDasharray="4 3"
                      strokeWidth={1.25}
                      dot={false}
                      isAnimationActive={false}
                      name="Raw altitude"
                    />
                    <Line
                      type="monotone"
                      dataKey="altitude_filtered_m"
                      stroke="#60a5fa"
                      strokeWidth={2.25}
                      dot={false}
                      isAnimationActive={false}
                      name="EKF filtered"
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <div className={`h-64 ${CARD_INNER} p-4`}>
                  <h3 className="text-xs font-semibold text-neutral-400 mb-2 px-1">
                    Velocity (m/s) — EKF Estimate
                  </h3>
                  <ResponsiveContainer width="100%" height="82%">
                    <LineChart data={playbackFrames} margin={{ ...CHART_MARGIN, bottom: 16 }}>
                      <CartesianGrid stroke={GRID_STROKE} />
                      <XAxis
                        dataKey="timestamp"
                        type="number"
                        domain={["dataMin", "dataMax"]}
                        tickFormatter={formatTime}
                        stroke={AXIS_STROKE}
                        tick={AXIS_TICK}
                        label={{ value: "Time (s)", position: "bottom", offset: 0, fill: "#888", fontSize: 11 }}
                      />
                      <YAxis
                        stroke={AXIS_STROKE}
                        tick={AXIS_TICK}
                        tickFormatter={formatWhole}
                        width={44}
                      />
                      <Tooltip
                        contentStyle={TOOLTIP_STYLE}
                        labelFormatter={(v) => `t = ${formatTime(Number(v))}s`}
                      />
                      <Line
                        type="monotone"
                        dataKey="velocity_filtered_ms"
                        stroke="#f472b6"
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                        name="EKF velocity"
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                <div className={`h-64 ${CARD_INNER} p-4`}>
                  <h3 className="text-xs font-semibold text-neutral-400 mb-2 px-1">
                    Accelerometer Bias (m/s²) — Estimated Drift
                  </h3>
                  <ResponsiveContainer width="100%" height="82%">
                    <LineChart data={playbackFrames} margin={{ ...CHART_MARGIN, bottom: 16 }}>
                      <CartesianGrid stroke={GRID_STROKE} />
                      <XAxis
                        dataKey="timestamp"
                        type="number"
                        domain={["dataMin", "dataMax"]}
                        tickFormatter={formatTime}
                        stroke={AXIS_STROKE}
                        tick={AXIS_TICK}
                        label={{ value: "Time (s)", position: "bottom", offset: 0, fill: "#888", fontSize: 11 }}
                      />
                      <YAxis
                        stroke={AXIS_STROKE}
                        tick={AXIS_TICK}
                        tickFormatter={formatBias}
                        width={56}
                      />
                      <Tooltip
                        contentStyle={TOOLTIP_STYLE}
                        labelFormatter={(v) => `t = ${formatTime(Number(v))}s`}
                      />
                      <Line
                        type="monotone"
                        dataKey="accel_bias_est"
                        stroke="#fbbf24"
                        strokeWidth={2}
                        dot={false}
                        isAnimationActive={false}
                        name="Bias (m/s²)"
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>

                <div className={`h-64 ${CARD_INNER} p-4`}>
                  <h3 className="text-xs font-semibold text-neutral-400 mb-2 px-1">
                    Filter Innovation (m) — Measurement vs. Prediction
                  </h3>
                  <ResponsiveContainer width="100%" height="82%">
                    <LineChart data={playbackFrames} margin={{ ...CHART_MARGIN, bottom: 16 }}>
                      <CartesianGrid stroke={GRID_STROKE} />
                      <XAxis
                        dataKey="timestamp"
                        type="number"
                        domain={["dataMin", "dataMax"]}
                        tickFormatter={formatTime}
                        stroke={AXIS_STROKE}
                        tick={AXIS_TICK}
                        label={{ value: "Time (s)", position: "bottom", offset: 0, fill: "#888", fontSize: 11 }}
                      />
                      <YAxis
                        stroke={AXIS_STROKE}
                        tick={AXIS_TICK}
                        tickFormatter={formatWhole}
                        width={44}
                      />
                      <Tooltip
                        contentStyle={TOOLTIP_STYLE}
                        labelFormatter={(v) => `t = ${formatTime(Number(v))}s`}
                      />
                      <ReferenceLine y={0} stroke={AXIS_STROKE} strokeDasharray="4 3" />
                      <Line
                        type="monotone"
                        dataKey="innovation_m"
                        stroke="#2dd4bf"
                        strokeWidth={1.5}
                        dot={false}
                        isAnimationActive={false}
                        name="Innovation (m)"
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function MiniStat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: string;
}) {
  return (
    <div className={`${CARD_INNER} p-2`}>
      <div className="text-[10px] text-neutral-500 uppercase">{label}</div>
      <div className={`text-sm font-mono ${accent || ""}`}>{value}</div>
    </div>
  );
}

function UploadIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="w-10 h-10 text-neutral-600"
    >
      <path d="M12 4v10" />
      <path d="M8 8l4-4 4 4" />
      <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
    </svg>
  );
}
