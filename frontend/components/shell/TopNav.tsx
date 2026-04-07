"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const navItems = [
  { href: "/signals", label: "Signals" },
  { href: "/branches", label: "Branches" },
];

export function TopNav() {
  const pathname = usePathname();

  return (
    <header className="fixed inset-x-0 top-0 z-50 h-10 border-b border-[--border] bg-[--bg-panel]/95 backdrop-blur">
      <div className="mx-auto flex h-full max-w-[1600px] items-center justify-between px-4 sm:px-6">
        <Link
          href="/signals"
          className="font-mono text-[11px] font-medium uppercase tracking-[0.24em] text-[--text-primary]"
        >
          HYPERTRADE■
        </Link>

        <nav className="flex h-full items-stretch gap-4 text-xs uppercase tracking-[0.18em] text-[--text-secondary]">
          {navItems.map(({ href, label }) => {
            const isActive =
              pathname === href || pathname.startsWith(`${href}/`);

            return (
              <Link
                key={href}
                href={href}
                className={[
                  "inline-flex items-center border-b-2 px-1 transition-colors",
                  isActive
                    ? "border-[--red-accent] text-[--text-primary]"
                    : "border-transparent text-[--text-secondary] hover:text-[--text-primary]",
                ].join(" ")}
              >
                {label}
              </Link>
            );
          })}
        </nav>
      </div>
    </header>
  );
}

export default TopNav;
