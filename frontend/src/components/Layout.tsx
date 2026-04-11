import { NavLink, Outlet, useLocation } from "react-router-dom";
import {
  LayoutDashboard,
  Film,
  Sparkles,
} from "lucide-react";

export default function Layout() {
  const location = useLocation();
  const isProject = location.pathname.startsWith("/project/");

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h1>TikTok Engine</h1>
          <p>AI Video Editor</p>
        </div>
        <nav className="sidebar-nav">
          <NavLink to="/" className={({ isActive }) => `nav-link ${isActive && !isProject ? "active" : ""}`}>
            <LayoutDashboard size={18} />
            Dashboard
          </NavLink>
          {isProject && (
            <>
              <div style={{ padding: "12px 12px 4px", fontSize: 11, color: "var(--text-dim)", textTransform: "uppercase", letterSpacing: 1 }}>
                Current Project
              </div>
              <NavLink to={location.pathname} className="nav-link active">
                <Film size={18} />
                Editor
              </NavLink>
            </>
          )}
          <div style={{ flex: 1 }} />
          <div style={{ padding: "16px 12px", fontSize: 12, color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 8 }}>
            <Sparkles size={14} />
            Powered by Claude AI
          </div>
        </nav>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}
