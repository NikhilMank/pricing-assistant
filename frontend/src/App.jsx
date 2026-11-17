import { useEffect, useState } from "react";
import Overview from "./Overview.jsx";
import ProductView from "./ProductView.jsx";
import Refit from "./Refit.jsx";

// The address bar decides what is shown: "#/product/SKU-1000/DE" is a product page, "#/refit/SKU-1000/DE" the
// custom recalculation page, anything else the overview.
function readHash() {
  const m = window.location.hash.match(/^#\/(product|refit)\/([^/]+)\/([A-Z]{2})/);
  return m ? { page: m[1], id: m[2], market: m[3] } : null;
}

export default function App() {
  const [route, setRoute] = useState(readHash());

  useEffect(() => {
    const onChange = () => setRoute(readHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const custom = route?.page === "refit"; // the custom page gets its own colours so nobody mistakes it for the official one
  return (
    <>
      {custom && <div className="custom-bar" />}
      <div className={custom ? "page custom" : "page"}>
        <header className="top">
          <a href="#/" className="brand">Pricing Assistant</a>
          <span className="sub">Price recommendations from sales data, explained by AI agents</span>
          {custom && <span className="custom-tag">Custom recalculation</span>}
        </header>
        {custom ? <Refit id={route.id} market={route.market} />
          : route ? <ProductView id={route.id} market={route.market} /> : <Overview />}
        <footer className="foot">
          Estimates are approximate and based on past sales. Promotions can make buyers look more price-sensitive than
          they would be for a lasting price change, so recommendations are small by design. A real A/B test is the
          next step before changing prices.
        </footer>
      </div>
    </>
  );
}
