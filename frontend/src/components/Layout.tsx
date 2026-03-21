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

function LogoMark() {
  return (
    <svg viewBox="0 0 28 28" width="28" height="28" className="flex-shrink-0">
      <defs>
        <radialGradient id="sidebarGlow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#3a8fff" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#0a0a12" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="28" height="28" rx="6" fill="#0a0e18" />
      <rect
        width="28"
        height="28"
        rx="6"
        fill="url(#sidebarGlow)"
      />
      {/* circuit traces */}
      <line x1="3" y1="7" x2="8" y2="7" stroke="#1e4a7a" strokeWidth="0.5" opacity="0.5" />
      <line x1="8" y1="7" x2="8" y2="12" stroke="#1e4a7a" strokeWidth="0.5" opacity="0.5" />
      <line x1="20" y1="21" x2="25" y2="21" stroke="#1e4a7a" strokeWidth="0.5" opacity="0.5" />
      <line x1="20" y1="16" x2="20" y2="21" stroke="#1e4a7a" strokeWidth="0.5" opacity="0.5" />
      {/* center glow */}
      <circle cx="14" cy="14" r="7" fill="#1e508c" opacity="0.12" />
      <circle cx="14" cy="14" r="4" fill="#3a8fff" opacity="0.06" />
      {/* PJ */}
      <text
        x="14"
        y="18"
        textAnchor="middle"
        fontFamily="system-ui, sans-serif"
        fontSize="10"
        fontWeight="700"
        fill="#e0e8f0"
      >
        PJ
      </text>
      {/* corner nodes */}
      <circle cx="5" cy="5" r="0.8" fill="#00c8ff" opacity="0.6" />
      <circle cx="23" cy="23" r="0.8" fill="#00c8ff" opacity="0.6" />
      <circle cx="23" cy="5" r="0.6" fill="#2a6aaa" opacity="0.4" />
      <circle cx="5" cy="23" r="0.6" fill="#2a6aaa" opacity="0.4" />
    </svg>
  );
}

export default function Layout() {
  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <nav className="flex w-56 flex-col border-r border-border-primary bg-bg-secondary">
        <div className="flex h-14 items-center gap-2.5 border-b border-border-primary px-4">
          <LogoMark />
          <div className="flex flex-col">
            <span className="text-sm font-semibold text-text-primary leading-tight">
              Prime Jennie
            </span>
            <span className="text-[10px] font-mono text-accent-cyan/50 tracking-wider">
              TRADING SYSTEM
            </span>
          </div>
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
