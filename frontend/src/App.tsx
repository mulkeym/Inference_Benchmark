import { BrowserRouter, NavLink, Route, Routes } from "react-router";
import EndpointsPage from "./pages/EndpointsPage";
import HistoryPage from "./pages/HistoryPage";
import NewTestPage from "./pages/NewTestPage";
import TestPage from "./pages/TestPage";

export default function App() {
  return <BrowserRouter>
    <nav className="topnav">
      <span className="brand">Inference Benchmark</span>
      <NavLink to="/" end>New Test</NavLink>
      <NavLink to="/history">History</NavLink>
      <NavLink to="/endpoints">Endpoints</NavLink>
    </nav>
    <main><Routes>
      <Route path="/" element={<NewTestPage />} />
      <Route path="/tests/:id" element={<TestPage />} />
      <Route path="/history" element={<HistoryPage />} />
      <Route path="/endpoints" element={<EndpointsPage />} />
    </Routes></main>
  </BrowserRouter>;
}
