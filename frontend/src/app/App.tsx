import { BrowserRouter, Route, Routes } from "react-router-dom";

import { TrainingModeBanner } from "../components/TrainingModeBanner";
import { DiagnosticsPanel } from "../features/diagnostics/DiagnosticsPanel";
import { ForecastDashboard } from "../features/forecast/ForecastDashboard";

export function App() {
  const homeContent = (
    <>
      <ForecastDashboard />
      <DiagnosticsPanel />
    </>
  );

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
            <Route path="/" element={homeContent} />
            <Route path="/stats" element={homeContent} />
            <Route path="*" element={homeContent} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}
