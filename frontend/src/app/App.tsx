import { useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { TrainingModeBanner } from "../components/TrainingModeBanner";
import { DiagnosticsPanel } from "../features/diagnostics/DiagnosticsPanel";
import { ForecastDashboard } from "../features/forecast/ForecastDashboard";

type PageTab = "forecast" | "diagnostics";

function HomePage() {
  const [activePageTab, setActivePageTab] = useState<PageTab>("forecast");

  return (
    <>
      {/* Page-level Tab Navigation */}
      <div className="page-tab-navigation">
        <button
          type="button"
          className={`page-tab-button ${activePageTab === "forecast" ? "active" : ""}`}
          onClick={() => setActivePageTab("forecast")}
        >
          Forecast Dashboard
        </button>
        <button
          type="button"
          className={`page-tab-button ${activePageTab === "diagnostics" ? "active" : ""}`}
          onClick={() => setActivePageTab("diagnostics")}
        >
          Diagnostics & Controls
        </button>
      </div>

      {/* Page Content */}
      {activePageTab === "forecast" && <ForecastDashboard />}
      {activePageTab === "diagnostics" && <DiagnosticsPanel />}
    </>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <div className="page">
        <TrainingModeBanner />
        <header className="hero">
          <h1>Agile Predict</h1>
          <p>React and Vite migration foundation is active.</p>
        </header>

        <main>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/stats" element={<HomePage />} />
            <Route path="*" element={<HomePage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
