"use client"; // Next.js directive to indicate this is a client-side component

import { useEffect, useRef, useState } from "react"; //react hooks for managing state and side effects
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts"; // Recharts library for rendering charts

interface TelemetryFrame {
  timestamp: string;
  mission_elapsed_time_s: number;
  altitude_m: number;
  velocity_ms: number;
  accel_x_ms2: number;
  accel_y_ms2: number;
  accel_z_ms2: number;
  gyro_x_rads: number;
  gyro_y_rads: number;
  gyro_z_rads: number;
  phase: "boost" | "coast" | "descent";
} // TypeScript interface defining the structure of a telemetry frame

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/telemetry"; 
// WebSocket URL, defaulting to localhost if not provided in environment variables
const MAX_POINTS = 500; // rolling window so the chart doesn't choke on long flights

const PHASE_COLORS: Record<string, string> = {
  boost: "bg-orange-600",
  coast: "bg-blue-600",
  descent: "bg-purple-600",
}; // Mapping of flight phases to background colors for UI display

export default function Home() {
  const [frames, setFrames] = useState<TelemetryFrame[]>([]); // State to hold the array of telemetry frames received from the WebSocket
  const [connected, setConnected] = useState(false); // State to track the connection status of the WebSocket
  const wsRef = useRef<WebSocket | null>(null); // Reference to hold the WebSocket instance across renders
  // State to hold telemetry frames, connection status, and a reference to the WebSocket instance

  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws; // Store the WebSocket instance in a ref to persist across renders

    ws.onopen = () => setConnected(true); // Update connection status when WebSocket opens
    ws.onclose = () => setConnected(false); // Update connection status when WebSocket closes
    ws.onerror = (err) => console.error("WebSocket error:", err); // Log any WebSocket errors to the console

    ws.onmessage = (event) => {
      try {
        const frame: TelemetryFrame = JSON.parse(event.data);
        setFrames((prev) => {
          const next = [...prev, frame];
          return next.length > MAX_POINTS ? next.slice(-MAX_POINTS) : next;
        });
      } catch (e) {
        console.error("Failed to parse frame:", e);
      }
    };

    return () => {
      ws.close();
    };
  }, []);

  const latest = frames[frames.length - 1]; // Get the latest telemetry frame for display in the UI

  return (
    <main className="min-h-screen bg-black text-white p-8"> 
      <div className="flex items-center gap-3 mb-6"> 
        <h1 className="text-2xl font-bold">VESPR Telemetry</h1> 
        <span
          className={`text-xs px-2 py-1 rounded ${
            connected ? "bg-green-600" : "bg-red-600"
          }`}
        >
          {connected ? "LIVE" : "DISCONNECTED"}
        </span>
        {latest && (
          <span
            className={`text-xs px-2 py-1 rounded uppercase ${
              PHASE_COLORS[latest.phase] || "bg-neutral-600" // Use the corresponding color for the current flight phase, defaulting to neutral if unknown
            }`}
          >
            {latest.phase}
          </span>
        )}
      </div>

      {latest && (
        <div className="grid grid-cols-4 gap-4 mb-6">
          <StatCard label="MET (s)" value={latest.mission_elapsed_time_s.toFixed(1)} />
          <StatCard label="Altitude (m)" value={latest.altitude_m.toFixed(1)} />
          <StatCard label="Velocity (m/s)" value={latest.velocity_ms.toFixed(1)} />
          <StatCard label="Accel Z (m/s²)" value={latest.accel_z_ms2.toFixed(2)} />
        </div>
      )}

      <div className="w-full h-96 bg-neutral-900 rounded-lg p-4">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={frames}>
            <CartesianGrid strokeDasharray="3 3" stroke="#333" />
            <XAxis
              dataKey="mission_elapsed_time_s"
              stroke="#888"
              label={{ value: "MET (s)", position: "insideBottom", offset: -5 }}
            />
            <YAxis
              yAxisId="altitude"
              stroke="#60a5fa"
              label={{ value: "Altitude (m)", angle: -90, position: "insideLeft" }}
            />
            <YAxis
              yAxisId="velocity"
              orientation="right"
              stroke="#f472b6"
              label={{ value: "Velocity (m/s)", angle: 90, position: "insideRight" }}
            />
            <Tooltip contentStyle={{ backgroundColor: "#1a1a1a", border: "1px solid #333" }} />
            <Legend />
            <Line
              yAxisId="altitude"
              type="monotone"
              dataKey="altitude_m"
              stroke="#60a5fa"
              dot={false}
              isAnimationActive={false}
              name="Altitude (m)"
            />
            <Line
              yAxisId="velocity"
              type="monotone"
              dataKey="velocity_ms"
              stroke="#f472b6"
              dot={false}
              isAnimationActive={false}
              name="Velocity (m/s)"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </main>
  ); // Render the main telemetry dashboard with connection status, latest stats, and a line chart of altitude and velocity over time
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-neutral-900 rounded-lg p-4">
      <div className="text-xs text-neutral-400 uppercase">{label}</div>
      <div className="text-2xl font-mono">{value}</div>
    </div>
  ); // Component to display individual telemetry statistics in a card format
}