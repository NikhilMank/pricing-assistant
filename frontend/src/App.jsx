import { useEffect, useState } from "react";
import Overview from "./Overview.jsx";
import ProductView from "./ProductView.jsx";

// The address bar decides what is shown: "#/product/SKU-1000/DE" is a product page, anything else the overview.
function readHash() {
  const m = window.location.hash.match(/^#\/product\/([^/]+)\/([A-Z]{2})/);
  return m ? { id: m[1], market: m[2] } : null;
}

export default function App() {
  const [route, setRoute] = useState(readHash());

  useEffect(() => {
    const onChange = () => setRoute(readHash());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  return (
    <div className="page">
      <header className="top">
        <a href="#/" className="brand">Pricing Assistant</a>
        <span className="sub">Price recommendations from sales data, explained by AI agents</span>
      </header>
      {route ? <ProductView id={route.id} market={route.market} /> : <Overview />}
      <footer className="foot">
        Estimates are approximate and based on past sales. Promotions can make buyers look more price-sensitive than
        they would be for a lasting price change, so recommendations are small by design. A real A/B test is the
        next step before changing prices.
      </footer>
    </div>
  );
}
