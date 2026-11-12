import { useEffect, useRef } from "react";
import Plotly from "plotly.js-dist-min";

// Small wrapper: draws a Plotly chart and redraws it when data changes.
export default function Plot({ data, layout, height = 320 }) {
  const ref = useRef(null);

  useEffect(() => {
    const base = {
      margin: { l: 55, r: 55, t: 30, b: 45 },
      font: { family: "system-ui, sans-serif", size: 13, color: "#111827" },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      legend: { orientation: "h", y: -0.2 },
      hoverlabel: { namelength: -1, bgcolor: "#ffffff", bordercolor: "#52606d", font: { color: "#1f2933", size: 13 } },
    };
    // Stronger grid and axis lines than the Plotly default; each chart's own axis settings are merged on top
    const axis = { gridcolor: "#cbd2da", linecolor: "#6b7280", zerolinecolor: "#6b7280", tickfont: { color: "#111827" } };
    const merged = { ...base, ...layout };
    for (const key of ["xaxis", "yaxis", "yaxis2"]) {
      if (key === "yaxis2" && !layout.yaxis2) continue;
      merged[key] = { ...axis, ...(layout[key] || {}) };
    }
    Plotly.react(ref.current, data, merged, { displayModeBar: false, responsive: true });
  }, [data, layout]);

  // Free the chart when the component goes away. The node is saved first: React clears ref.current before cleanup.
  useEffect(() => {
    const node = ref.current;
    return () => Plotly.purge(node);
  }, []);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
