import { Outlet, NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  Briefcase,
  Search,
  Globe,
  Server,
} from "lucide-react";

const NAV_ITEMS = [
  { to: "/", icon: LayoutDashboard, label: "Overview" },
  { to: "/portfolio", icon: Briefcase, label: "Portfolio" },
  { to: "/scout", icon: Search, label: "Scout" },
  { to: "/macro", icon: Globe, label: "Macro" },
  { to: "/system", icon: Server, label: "System" },
];

export default function Layout() {
  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <nav className="flex w-56 flex-col border-r border-border-primary bg-bg-secondary">
        <div className="flex h-14 items-center gap-2 border-b border-border-primary px-4">
          <div className="h-7 w-7 rounded-lg bg-accent-blue flex items-center justify-center text-xs font-bold text-white">
            PJ
          </div>
          <span className="text-sm font-semibold text-text-primary">
            Prime Jennie
          </span>
        </div>

        <div className="flex flex-1 flex-col gap-1 p-3">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                  isActive
                    ? "bg-bg-tertiary text-accent-blue"
                    : "text-text-secondary hover:bg-bg-tertiary hover:text-text-primary"
                }`
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </div>

        <div className="border-t border-border-primary p-3">
          <p className="text-xs text-text-muted">v1.0.0</p>
        </div>
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-auto bg-bg-primary">
        <div className="mx-auto max-w-7xl p-6">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
