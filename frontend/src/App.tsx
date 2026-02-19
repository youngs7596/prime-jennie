import { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import LoadingSpinner from "./components/LoadingSpinner";

const Overview = lazy(() => import("./pages/Overview"));
const Portfolio = lazy(() => import("./pages/Portfolio"));
const Scout = lazy(() => import("./pages/Scout"));
const Macro = lazy(() => import("./pages/Macro"));
const System = lazy(() => import("./pages/System"));

export default function App() {
  return (
    <Suspense fallback={<LoadingSpinner />}>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<Overview />} />
          <Route path="/portfolio" element={<Portfolio />} />
          <Route path="/scout" element={<Scout />} />
          <Route path="/macro" element={<Macro />} />
          <Route path="/system" element={<System />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
