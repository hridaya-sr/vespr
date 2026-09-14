"use client"; // Next.js directive to indicate this is a client-side component

import CsvReplayPanel from "./components/CsvReplayPanel";

// This app has no live telemetry source — every chart and stat is driven
// entirely by an uploaded CSV flight log, replayed frame-by-frame through
// the backend's EKF. See CsvReplayPanel for all of the state and charting.
export default function Home() {
  return (
    <main className="min-h-screen bg-black text-white p-8">
      <div className="flex items-center gap-4 mb-6">
        <h1 className="text-2xl font-bold">VESPR Telemetry</h1>
      </div>

      <CsvReplayPanel />
    </main>
  );
}
